from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from axiz.pe.sql_agent.models.contracts import (
    AgentTraceStep,
    CostValidation,
    HumanFeedbackRequest,
    LLMApprovalEstimate,
    LLMUsageSummary,
    QueryResult,
    ReviewPayload,
    RunResponse,
    SecurityValidation,
    RunStatus,
    UserPrincipal,
    VisualizationSpec,
)
from axiz.pe.sql_agent.repositories.run_repository import RunRepository
from axiz.pe.sql_agent.repositories.session_repository import SessionRepository
from axiz.pe.sql_agent.services.llm_usage import (
    activate_llm_usage_collection,
    reset_llm_usage_collection,
)
from axiz.pe.sql_agent.services.message_format import feedback_content
from axiz.pe.sql_agent.tools.excel_export import ExcelExportTool

logger = structlog.get_logger(__name__)

_STAGE_LABELS: dict[str, tuple[str, str]] = {
    "classify": ("Intención clasificada", "Se identificó la intención y el dominio de datos."),
    "answer_capabilities": (
        "Capacidades preparadas",
        "Se construyó una respuesta sin ejecutar SQL.",
    ),
    "answer_conversation_context": (
        "Contexto de sesión recuperado",
        "Se respondió usando el historial persistido sin ejecutar SQL.",
    ),
    "explore_semantics": (
        "Catálogo explorado",
        "Se seleccionaron métricas, dimensiones y ejemplos relevantes.",
    ),
    "answer_catalog": ("Catálogo respondido", "Se respondió usando únicamente la capa semántica."),
    "generate_sql": ("SQL generado", "La consulta fue generada y pasará por controles técnicos."),
    "validate_security": (
        "Seguridad validada",
        "SQLGlot verificó operaciones, fuentes y límites permitidos.",
    ),
    "estimate_cost": (
        "Costo validado",
        "Se evaluó el plan de ejecución antes de solicitar aprobación.",
    ),
    "estimate_llm_approval": (
        "Consumo posterior estimado",
        "Se estimaron las llamadas LLM que ocurrirían después de aprobar el SQL.",
    ),
    "human_review": ("Revisión humana", "La ejecución está esperando una decisión del usuario."),
    "execute_sql": (
        "Consulta ejecutada",
        "La base de datos devolvió los resultados en modo de solo lectura.",
    ),
    "verify_result": (
        "Resultado verificado",
        "Se comprobaron consistencia, vacíos y posibles anomalías.",
    ),
    "explain": ("Respuesta preparada", "Se generó la explicación y la visualización."),
    "unsupported": ("Solicitud fuera de alcance", "No se generó ni ejecutó SQL."),
    "clarification": (
        "Aclaración requerida",
        "Se necesita información adicional antes de continuar.",
    ),
    "rejected": ("Consulta rechazada", "La consulta no fue ejecutada."),
}


class AgentWorkflowService:
    def __init__(
        self,
        *,
        checkpoint_dsn: str,
        graph_builder,
        sessions: SessionRepository,
        runs: RunRepository,
        excel_exports: ExcelExportTool,
    ) -> None:
        self.checkpoint_dsn = checkpoint_dsn
        self.graph_builder = graph_builder
        self.sessions = sessions
        self.runs = runs
        self.excel_exports = excel_exports
        self._checkpointer_context = None
        self.checkpointer: AsyncPostgresSaver | None = None
        self.graph = None

    async def start(self) -> None:
        self._checkpointer_context = AsyncPostgresSaver.from_conn_string(self.checkpoint_dsn)
        self.checkpointer = await self._checkpointer_context.__aenter__()
        await self.checkpointer.setup()
        self.graph = self.graph_builder.compile(checkpointer=self.checkpointer)

    async def close(self) -> None:
        if self._checkpointer_context is not None:
            await self._checkpointer_context.__aexit__(None, None, None)

    async def start_run(
        self,
        *,
        principal: UserPrincipal,
        session_id: UUID,
        question: str,
    ) -> RunResponse:
        run_id, state = await self._prepare_run(principal, session_id, question)
        collector, usage_token = activate_llm_usage_collection()
        try:
            result = await self.graph.ainvoke(
                state,
                config={"configurable": {"thread_id": str(run_id)}},
            )
            result["llm_usage"] = collector.summary().model_dump(mode="json")
            response = await self._to_response(run_id, session_id, result)
            await self._persist_response(response, result)
            return response
        except Exception as exc:
            state["llm_usage"] = collector.summary().model_dump(mode="json")
            return await self._handle_failure(run_id, session_id, state, exc)
        finally:
            reset_llm_usage_collection(usage_token)

    async def stream_start_run(
        self,
        *,
        principal: UserPrincipal,
        session_id: UUID,
        question: str,
    ) -> AsyncIterator[dict[str, Any]]:
        run_id, state = await self._prepare_run(principal, session_id, question)
        yield {
            "type": "run_started",
            "data": {"run_id": str(run_id), "session_id": str(session_id)},
        }
        async for event in self._stream_graph_execution(
            run_id=run_id,
            session_id=session_id,
            initial_input=state,
            fallback_state=state,
        ):
            yield event

    async def resume_run(
        self,
        *,
        principal: UserPrincipal,
        run_id: UUID,
        feedback: HumanFeedbackRequest,
    ) -> RunResponse:
        run, session_id = await self._prepare_resume(principal, run_id, feedback)
        previous_state = dict(run.get("state") or {})
        collector, usage_token = activate_llm_usage_collection(
            previous_state.get("llm_usage")
        )
        try:
            result = await self.graph.ainvoke(
                Command(
                    resume={
                        "decision": feedback.decision.value,
                        "comment": feedback.comment,
                    }
                ),
                config={"configurable": {"thread_id": str(run_id)}},
            )
            result["llm_usage"] = collector.summary().model_dump(mode="json")
            response = await self._to_response(run_id, session_id, result)
            await self._persist_response(response, result)
            return response
        except Exception as exc:
            previous_state["llm_usage"] = collector.summary().model_dump(mode="json")
            return await self._handle_failure(run_id, session_id, previous_state, exc)
        finally:
            reset_llm_usage_collection(usage_token)

    async def stream_resume_run(
        self,
        *,
        principal: UserPrincipal,
        run_id: UUID,
        feedback: HumanFeedbackRequest,
    ) -> AsyncIterator[dict[str, Any]]:
        run, session_id = await self._prepare_resume(principal, run_id, feedback)
        yield {
            "type": "run_resumed",
            "data": {
                "run_id": str(run_id),
                "session_id": str(session_id),
                "decision": feedback.decision.value,
            },
        }
        command = Command(
            resume={
                "decision": feedback.decision.value,
                "comment": feedback.comment,
            }
        )
        async for event in self._stream_graph_execution(
            run_id=run_id,
            session_id=session_id,
            initial_input=command,
            fallback_state=dict(run.get("state") or {}),
        ):
            yield event

    async def get_run(self, principal: UserPrincipal, run_id: UUID) -> RunResponse:
        run = await self.runs.get(run_id, principal.user_id)
        if not run:
            raise PermissionError("Run not found or not owned by user")
        state = dict(run.get("state") or {})
        cached_response = state.get("_api_response")
        if cached_response:
            cached_response = dict(cached_response)
            cached_response["status"] = run["status"]
            cached_response["error"] = run.get("error") or cached_response.get("error")
            if run["status"] != RunStatus.AWAITING_APPROVAL.value:
                cached_response["review"] = None
            return RunResponse.model_validate(cached_response)
        return await self._to_response(run_id, UUID(str(run["session_id"])), state)

    async def _prepare_run(
        self,
        principal: UserPrincipal,
        session_id: UUID,
        question: str,
    ) -> tuple[UUID, dict[str, Any]]:
        if self.graph is None:
            raise RuntimeError("Workflow service is not started")
        await self.sessions.assert_owner(session_id, principal.user_id)
        history = await self.sessions.get_history(session_id)
        await self.sessions.add_message(
            session_id,
            "user",
            question,
            metadata={"message_type": "user_question"},
        )
        await self.sessions.auto_title_from_question(session_id, question)
        run_id = await self.runs.create(session_id, principal.user_id, question)
        state: dict[str, Any] = {
            "run_id": str(run_id),
            "session_id": str(session_id),
            "user_id": str(principal.user_id),
            "question": question,
            "conversation_history": history,
            "repair_attempts": 0,
            "status": "running",
        }
        return run_id, state

    async def _prepare_resume(
        self,
        principal: UserPrincipal,
        run_id: UUID,
        feedback: HumanFeedbackRequest,
    ) -> tuple[dict[str, Any], UUID]:
        if self.graph is None:
            raise RuntimeError("Workflow service is not started")
        run = await self.runs.get(run_id, principal.user_id)
        if not run:
            raise PermissionError("Run not found or not owned by user")
        if run["status"] != RunStatus.AWAITING_APPROVAL.value:
            raise ValueError(f"Run is not awaiting approval; current status is {run['status']}")
        session_id = UUID(str(run["session_id"]))
        await self.runs.add_feedback(
            run_id,
            principal.user_id,
            feedback.decision.value,
            feedback.comment,
        )
        await self.sessions.add_message(
            session_id,
            "user",
            feedback_content(feedback),
            metadata={
                "message_type": "human_feedback",
                "run_id": str(run_id),
                "decision": feedback.decision.value,
                "comment": feedback.comment,
                "exclude_from_context": True,
            },
        )
        return run, session_id

    async def _stream_graph_execution(
        self,
        *,
        run_id: UUID,
        session_id: UUID,
        initial_input: Any,
        fallback_state: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        if self.graph is None:
            raise RuntimeError("Workflow service is not started")
        config = {"configurable": {"thread_id": str(run_id)}}
        interrupts: list[Any] = []
        collector, usage_token = activate_llm_usage_collection(
            fallback_state.get("llm_usage")
        )
        last_usage_call_count = collector.summary().call_count
        try:
            async for chunk in self.graph.astream(
                initial_input,
                config=config,
                stream_mode="updates",
            ):
                if not isinstance(chunk, dict):
                    continue
                if "__interrupt__" in chunk:
                    raw_interrupts = chunk.get("__interrupt__") or []
                    interrupts.extend(list(raw_interrupts))
                    continue
                for node_name, update in chunk.items():
                    if node_name.startswith("__"):
                        continue
                    yield self._stage_event(node_name, update)
                    usage_summary = collector.summary()
                    if usage_summary.call_count != last_usage_call_count:
                        last_usage_call_count = usage_summary.call_count
                        yield {
                            "type": "llm_usage",
                            "data": usage_summary.model_dump(mode="json"),
                        }

            snapshot = await self.graph.aget_state(config)
            result = dict(getattr(snapshot, "values", {}) or {})
            if not interrupts:
                interrupts = self._snapshot_interrupts(snapshot)
            if interrupts:
                result["__interrupt__"] = interrupts
            result["llm_usage"] = collector.summary().model_dump(mode="json")

            response = await self._to_response(run_id, session_id, result)
            await self._persist_response(response, result)

            if response.status == RunStatus.AWAITING_APPROVAL:
                yield {"type": "review", "data": response.model_dump(mode="json")}
            elif response.answer:
                async for delta in self._answer_deltas(response.answer):
                    yield {"type": "answer_delta", "data": {"delta": delta}}

            yield {"type": "completed", "data": response.model_dump(mode="json")}
        except Exception as exc:
            fallback_state["llm_usage"] = collector.summary().model_dump(mode="json")
            response = await self._handle_failure(run_id, session_id, fallback_state, exc)
            yield {"type": "error", "data": {"message": str(exc)}}
            yield {"type": "completed", "data": response.model_dump(mode="json")}
        finally:
            reset_llm_usage_collection(usage_token)

    @staticmethod
    def _snapshot_interrupts(snapshot: Any) -> list[Any]:
        direct = list(getattr(snapshot, "interrupts", ()) or ())
        if direct:
            return direct
        collected: list[Any] = []
        for task in getattr(snapshot, "tasks", ()) or ():
            collected.extend(list(getattr(task, "interrupts", ()) or ()))
        return collected

    @staticmethod
    def _stage_event(node_name: str, update: Any) -> dict[str, Any]:
        label, detail = _STAGE_LABELS.get(
            node_name,
            (node_name.replace("_", " ").title(), "Etapa del workflow completada."),
        )
        summary: dict[str, Any] = {}
        if isinstance(update, dict):
            if update.get("domain"):
                summary["domain"] = update["domain"]
            if update.get("selected_examples") is not None:
                summary["example_count"] = len(update.get("selected_examples") or [])
            if update.get("query_result"):
                summary["row_count"] = update["query_result"].get("row_count")
            if update.get("security_validation"):
                security = update["security_validation"]
                summary["security_approved"] = security.get("approved")
                summary["statement_type"] = security.get("statement_type")
                summary["tables"] = security.get("tables", [])
                summary["enforced_limit"] = security.get("enforced_limit")
                summary["violations"] = security.get("violations", [])
            if update.get("cost_validation"):
                cost = update["cost_validation"]
                summary["cost_approved"] = cost.get("approved")
                summary["plan_cost"] = cost.get("total_cost")
                summary["max_plan_cost"] = cost.get("max_plan_cost")
                summary["plan_rows"] = cost.get("plan_rows")
                summary["max_node_rows"] = cost.get("max_node_rows")
                summary["max_plan_rows"] = cost.get("max_plan_rows")
                summary["relation_bytes"] = cost.get("relation_bytes")
                summary["max_relation_bytes"] = cost.get("max_relation_bytes")
                summary["plan_relations"] = cost.get("plan_relations", [])
                summary["warnings"] = cost.get("warnings", [])
            if update.get("llm_approval_estimate"):
                estimate = update["llm_approval_estimate"]
                summary["expected_llm_calls"] = estimate.get("expected_call_count")
                summary["estimated_future_tokens"] = estimate.get("estimated_total_tokens")
                summary["maximum_future_tokens"] = estimate.get("maximum_total_tokens")
        return {
            "type": "stage",
            "data": {
                "node": node_name,
                "label": label,
                "detail": detail,
                "summary": summary,
            },
        }

    @staticmethod
    async def _answer_deltas(answer: str) -> AsyncIterator[str]:
        tokens = re.findall(r"\S+\s*", answer)
        for index in range(0, len(tokens), 7):
            yield "".join(tokens[index : index + 7])
            await asyncio.sleep(0.015)

    async def _handle_failure(
        self,
        run_id: UUID,
        session_id: UUID,
        state: dict[str, Any],
        exc: Exception,
    ) -> RunResponse:
        failed_state = dict(state)
        failed_state.update({"status": "failed", "error": str(exc)})
        llm_usage = (
            LLMUsageSummary.model_validate(failed_state["llm_usage"])
            if failed_state.get("llm_usage")
            else None
        )
        response = RunResponse(
            run_id=run_id,
            session_id=session_id,
            status=RunStatus.FAILED,
            answer="No fue posible completar la solicitud.",
            error=str(exc),
            llm_usage=llm_usage,
        )
        try:
            await self._persist_response(response, failed_state)
        except Exception as persistence_exc:
            logger.exception(
                "failed_to_persist_agent_error",
                run_id=str(run_id),
                original_error=str(exc),
                persistence_error=str(persistence_exc),
            )
        return response

    async def _to_response(
        self,
        run_id: UUID,
        session_id: UUID,
        result: dict[str, Any],
    ) -> RunResponse:
        interrupts = result.get("__interrupt__") or []
        if interrupts:
            first = interrupts[0]
            payload = first.value if hasattr(first, "value") else first
            return RunResponse(
                run_id=run_id,
                session_id=session_id,
                status=RunStatus.AWAITING_APPROVAL,
                review=ReviewPayload.model_validate(payload),
                interpretation=payload.get("interpretation"),
                domain=payload.get("domain"),
                assumptions=payload.get("assumptions", []),
                source_objects=payload.get("source_objects", []),
                sql=payload.get("sql"),
                trace=self._build_trace(result),
                security_validation=(
                    SecurityValidation.model_validate(result["security_validation"])
                    if result.get("security_validation")
                    else None
                ),
                cost_validation=(
                    CostValidation.model_validate(result["cost_validation"])
                    if result.get("cost_validation")
                    else None
                ),
                llm_usage=(
                    LLMUsageSummary.model_validate(result["llm_usage"])
                    if result.get("llm_usage")
                    else None
                ),
                llm_approval_estimate=(
                    LLMApprovalEstimate.model_validate(result["llm_approval_estimate"])
                    if result.get("llm_approval_estimate")
                    else None
                ),
            )

        raw_status = result.get("status", "failed")
        try:
            status = RunStatus(raw_status)
        except ValueError:
            status = RunStatus.FAILED
        query_result = (
            QueryResult.model_validate(result["query_result"])
            if result.get("query_result")
            else None
        )
        visualization = (
            VisualizationSpec.model_validate(result["visualization"])
            if result.get("visualization")
            else None
        )
        security_validation = (
            SecurityValidation.model_validate(result["security_validation"])
            if result.get("security_validation")
            else None
        )
        cost_validation = (
            CostValidation.model_validate(result["cost_validation"])
            if result.get("cost_validation")
            else None
        )
        llm_usage = (
            LLMUsageSummary.model_validate(result["llm_usage"])
            if result.get("llm_usage")
            else None
        )
        llm_approval_estimate = (
            LLMApprovalEstimate.model_validate(result["llm_approval_estimate"])
            if result.get("llm_approval_estimate")
            else None
        )
        export = self.excel_exports.availability(query_result, status)
        return RunResponse(
            run_id=run_id,
            session_id=session_id,
            status=status,
            interpretation=result.get("interpretation"),
            domain=result.get("domain"),
            assumptions=result.get("assumptions", []),
            source_objects=result.get("source_objects", []),
            answer=result.get("answer"),
            key_findings=result.get("key_findings", []),
            caveats=result.get("caveats", []),
            result=query_result,
            visualization=visualization,
            sql=result.get("generated_sql"),
            error=result.get("error"),
            trace=self._build_trace(result),
            security_validation=security_validation,
            cost_validation=cost_validation,
            llm_usage=llm_usage,
            llm_approval_estimate=llm_approval_estimate,
            export=export,
        )

    @staticmethod
    def _build_trace(result: dict[str, Any]) -> list[AgentTraceStep]:
        """Build a safe, user-facing execution trace.

        This intentionally summarizes decisions, selected metadata, validations and tool
        results. It does not expose hidden model reasoning or chain-of-thought tokens.
        """
        trace: list[AgentTraceStep] = []

        def add(stage: str, label: str, detail: str, summary: dict[str, Any]) -> None:
            compact = {key: value for key, value in summary.items() if value not in (None, [], {})}
            trace.append(
                AgentTraceStep(stage=stage, label=label, detail=detail, summary=compact)
            )

        if result.get("intent"):
            add(
                "classify",
                "Intención y dominio",
                "La solicitud fue clasificada y vinculada con un dominio publicado.",
                {
                    "intent": result.get("intent"),
                    "domain": result.get("domain"),
                    "confidence": result.get("domain_confidence"),
                },
            )

        semantic_context = result.get("semantic_context") or {}
        if semantic_context or result.get("selected_examples") is not None:
            add(
                "explore_semantics",
                "Contexto semántico",
                "Se seleccionaron definiciones, métricas, dimensiones y ejemplos del catálogo.",
                {
                    "catalog_hits": len(semantic_context.get("catalog_hits", [])),
                    "examples": len(result.get("selected_examples") or []),
                    "metrics": result.get("selected_metrics", []),
                    "dimensions": result.get("selected_dimensions", []),
                    "sources": result.get("source_objects", []),
                },
            )

        if result.get("generated_sql"):
            add(
                "generate_sql",
                "Consulta SQL",
                "Se generó una consulta basada en el contrato semántico y el feedback disponible.",
                {
                    "revision": result.get("review_revision", 1),
                    "interpretation": result.get("interpretation"),
                    "assumptions": result.get("assumptions", []),
                },
            )

        security = result.get("security_validation") or {}
        if security:
            add(
                "validate_security",
                "Validación de seguridad",
                "SQLGlot revisó tipo de sentencia, fuentes, columnas y políticas de solo lectura.",
                {
                    "approved": security.get("approved"),
                    "tables": security.get("tables", []),
                    "violations": security.get("violations", []),
                },
            )

        cost = result.get("cost_validation") or {}
        if cost:
            add(
                "estimate_cost",
                "Validación de costo",
                "Se evaluó el plan de ejecución antes de consultar la fuente de datos.",
                {
                    "approved": cost.get("approved"),
                    "planner_cost": cost.get("total_cost"),
                    "estimated_rows": cost.get("plan_rows"),
                    "relation_bytes": cost.get("relation_bytes"),
                    "warnings": cost.get("warnings", []),
                },
            )

        query_result = result.get("query_result") or {}
        if query_result:
            add(
                "execute_sql",
                "Ejecución",
                "La consulta se ejecutó con una conexión de solo lectura.",
                {
                    "rows": query_result.get("row_count"),
                    "elapsed_ms": query_result.get("elapsed_ms"),
                    "truncated": query_result.get("truncated"),
                },
            )

        verification = result.get("verification") or {}
        if verification:
            add(
                "verify_result",
                "Verificación",
                "Se revisó la consistencia del resultado antes de explicarlo.",
                {
                    "valid": verification.get("valid"),
                    "confidence": verification.get("confidence"),
                    "observations": verification.get("observations", []),
                    "caveats": verification.get("caveats", []),
                },
            )

        approval_estimate = result.get("llm_approval_estimate") or {}
        if approval_estimate:
            add(
                "estimate_llm_approval",
                "Estimación LLM al aprobar",
                "Se proyectaron las llamadas posteriores sin ejecutar todavía los modelos.",
                {
                    "expected_calls": approval_estimate.get("expected_call_count"),
                    "estimated_tokens": approval_estimate.get("estimated_total_tokens"),
                    "maximum_tokens": approval_estimate.get("maximum_total_tokens"),
                    "projected_rows": approval_estimate.get("projected_result_rows"),
                },
            )

        usage = result.get("llm_usage") or {}
        if usage:
            add(
                "llm_usage",
                "Consumo LLM",
                "Se consolidó el uso real reportado por cada proveedor y la estimación previa.",
                {
                    "calls": usage.get("call_count"),
                    "actual_input_tokens": usage.get("actual_input_tokens"),
                    "actual_output_tokens": usage.get("actual_output_tokens"),
                    "actual_total_tokens": usage.get("actual_total_tokens"),
                    "estimated_max_total_tokens": usage.get(
                        "estimated_max_total_tokens"
                    ),
                },
            )

        if result.get("answer"):
            add(
                "explain",
                "Respuesta y visualización",
                "Se preparó una explicación basada únicamente en el resultado verificado.",
                {"visualization": (result.get("visualization") or {}).get("type")},
            )
        return trace

    async def _persist_response(self, response: RunResponse, state: dict[str, Any]) -> None:
        state = dict(state)
        response_payload = response.model_dump(mode="json")
        state["_api_response"] = response_payload
        await self.runs.update(
            response.run_id,
            response.status.value,
            state=state,
            error=response.error,
        )

        if response.status == RunStatus.AWAITING_APPROVAL and response.review:
            await self.sessions.add_message(
                response.session_id,
                "assistant",
                "Preparé una consulta SQL para tu revisión antes de ejecutarla.",
                metadata={
                    "message_type": "sql_review",
                    "run_id": str(response.run_id),
                    "status": response.status.value,
                    "review": response.review.model_dump(mode="json"),
                    "payload": response_payload,
                    "exclude_from_context": True,
                },
            )
            return

        if response.status in {
            RunStatus.COMPLETED,
            RunStatus.REJECTED,
            RunStatus.NEEDS_CLARIFICATION,
            RunStatus.FAILED,
        }:
            content = response.answer or response.error or "La ejecución terminó sin respuesta."
            await self.sessions.add_message(
                response.session_id,
                "assistant",
                content,
                metadata={
                    "message_type": (
                        "agent_error" if response.status == RunStatus.FAILED else "agent_response"
                    ),
                    "run_id": str(response.run_id),
                    "status": response.status.value,
                    "payload": response_payload,
                    "exclude_from_context": response.status == RunStatus.FAILED,
                },
            )
