from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from axiz.pe.sql_agent.models.contracts import (
    AgentTraceStep,
    AutonomousBudget,
    AutonomousBudgetUsage,
    AutonomousInvestigationSummary,
    CriticReviewOutput,
    EvidenceBackedFinding,
    InvestigationEvidence,
    InvestigationTrajectoryEvent,
    SpecialistQueryProposal,
    InvestigationPlan,
    SupervisorDecision,
    ContextResolutionOutput,
    ConversationMemory,
    CostValidation,
    FeedbackComplianceResult,
    HumanFeedbackRequest,
    LLMApprovalEstimate,
    LLMUsageSummary,
    QueryResult,
    ReviewPayload,
    RunResponse,
    SecurityValidation,
    SqlFeedbackApplication,
    SqlFeedbackPlan,
    RunStatus,
    UserPrincipal,
    VisualizationSpec,
)
from axiz.pe.sql_agent.repositories.conversation_memory_repository import (
    ConversationMemoryRepository,
)
from axiz.pe.sql_agent.repositories.run_repository import (
    RunConflictError,
    RunLeaseError,
    RunRepository,
)
from axiz.pe.sql_agent.repositories.session_repository import SessionRepository
from axiz.pe.sql_agent.services.conversation_memory import StructuredConversationMemoryService
from axiz.pe.sql_agent.services.llm_usage import (
    activate_llm_usage_collection,
    reset_llm_usage_collection,
)
from axiz.pe.sql_agent.services.message_format import feedback_content
from axiz.pe.sql_agent.services.run_execution import RunExecutionCoordinator
from axiz.pe.sql_agent.tools.excel_export import ExcelExportTool

logger = structlog.get_logger(__name__)

_STAGE_LABELS: dict[str, tuple[str, str]] = {
    "resolve_context": (
        "Contexto estructurado resuelto",
        "Se combinó la solicitud actual con la memoria analítica persistida cuando correspondía.",
    ),
    "initialize_society": (
        "Sociedad gobernada inicializada",
        "Se fijaron especialistas, HITL obligatorio y presupuestos inmutables.",
    ),
    "plan_investigation": (
        "Investigación planificada",
        "El planificador creó las tareas mínimas de evidencia.",
    ),
    "supervisor_review": (
        "Supervisor revisó la investigación",
        "El supervisor decidió delegar, pedir evidencia o finalizar.",
    ),
    "collect_specialist_wave": (
        "Ola paralela de especialistas completada",
        "Los subgrafos especialistas prepararon propuestas aisladas y gobernadas.",
    ),
    "select_next_proposal": (
        "Propuesta seleccionada para HITL",
        "La siguiente consulta validada quedó lista para aprobación humana.",
    ),
    "reject_autonomous_proposal": (
        "Propuesta autónoma rechazada",
        "La propuesta fue descartada sin ejecutar SQL.",
    ),
    "record_evidence": (
        "Evidencia registrada",
        "El resultado aprobado quedó asociado con su tarea y SQL.",
    ),
    "critic_review": (
        "Evidencia criticada",
        "El agente crítico evaluó suficiencia, contradicciones y faltantes.",
    ),
    "synthesize_investigation": (
        "Investigación sintetizada",
        "El supervisor produjo una respuesta basada solo en evidencia verificada.",
    ),
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
    "interpret_follow_up": (
        "Seguimiento analítico interpretado",
        "La solicitud se convirtió en un cambio gobernado sobre el SQL anterior.",
    ),
    "interpret_feedback": (
        "Feedback interpretado",
        "La corrección humana se convirtió en un plan semántico tipado.",
    ),
    "generate_sql": ("SQL generado", "La consulta fue generada y pasará por controles técnicos."),
    "apply_feedback": (
        "Cambios estructurales aplicados",
        "Se aplicaron sobre el AST los cambios determinísticos seguros.",
    ),
    "validate_feedback_compliance": (
        "Cumplimiento del feedback validado",
        "Se comprobó que la revisión aplica todos los cambios solicitados.",
    ),
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
        memories: ConversationMemoryRepository,
        memory_service: StructuredConversationMemoryService,
        runs: RunRepository,
        excel_exports: ExcelExportTool,
        execution_coordinator: RunExecutionCoordinator,
        max_concurrent_runs_per_user: int,
        max_llm_tokens: int,
        active_execution_timeout_seconds: int,
    ) -> None:
        self.checkpoint_dsn = checkpoint_dsn
        self.graph_builder = graph_builder
        self.sessions = sessions
        self.memories = memories
        self.memory_service = memory_service
        self.runs = runs
        self.excel_exports = excel_exports
        self.execution_coordinator = execution_coordinator
        self.max_concurrent_runs_per_user = max_concurrent_runs_per_user
        self.max_llm_tokens = max_llm_tokens
        self.active_execution_timeout_seconds = active_execution_timeout_seconds
        self._checkpointer_context = None
        self.checkpointer: AsyncPostgresSaver | None = None
        self.graph = None

    async def start(self) -> None:
        await self.runs.recover_stale_runs()
        self._checkpointer_context = AsyncPostgresSaver.from_conn_string(self.checkpoint_dsn)
        self.checkpointer = await self._checkpointer_context.__aenter__()
        await self.checkpointer.setup()
        self.graph = self.graph_builder.compile(checkpointer=self.checkpointer)

    async def close(self) -> None:
        if self._checkpointer_context is not None:
            await self._checkpointer_context.__aexit__(None, None, None)

    def _remaining_active_execution_seconds(self, state: dict[str, Any]) -> float:
        consumed = float(
            (state.get("autonomous_budget_usage") or {}).get(
                "active_execution_seconds", 0.0
            )
        )
        remaining = float(self.active_execution_timeout_seconds) - consumed
        if remaining <= 0:
            raise TimeoutError(
                "Se agotó el presupuesto de tiempo activo de la investigación"
            )
        return remaining

    @staticmethod
    def _add_active_execution_time(
        result: dict[str, Any],
        elapsed: float,
        previous_state: dict[str, Any] | None = None,
    ) -> None:
        usage = dict(result.get("autonomous_budget_usage") or {})
        previous_usage = dict((previous_state or {}).get("autonomous_budget_usage") or {})
        current = float(usage.get("active_execution_seconds") or 0.0)
        previous = float(previous_usage.get("active_execution_seconds") or 0.0)
        usage["active_execution_seconds"] = max(current, previous) + elapsed
        result["autonomous_budget_usage"] = usage

    async def _latest_checkpoint_state(
        self, run_id: UUID, fallback: dict[str, Any]
    ) -> dict[str, Any]:
        if self.graph is None:
            return dict(fallback)
        try:
            snapshot = await self.graph.aget_state(
                {"configurable": {"thread_id": str(run_id)}}
            )
            values = dict(getattr(snapshot, "values", {}) or {})
            return {**fallback, **values}
        except Exception:
            return dict(fallback)

    async def start_run(
        self,
        *,
        principal: UserPrincipal,
        session_id: UUID,
        question: str,
        idempotency_key: str | None = None,
    ) -> RunResponse:
        run_id, state, lease_owner, replay = await self._prepare_run(
            principal, session_id, question, idempotency_key=idempotency_key
        )
        if replay:
            response = await self.get_run(principal, run_id)
            response.idempotent_replay = True
            return response
        collector, usage_token = activate_llm_usage_collection(max_total_tokens=self.max_llm_tokens)
        started = time.perf_counter()
        try:
            async with self.execution_coordinator.execution(run_id, lease_owner):
                async with asyncio.timeout(self._remaining_active_execution_seconds(state)):
                    result = await self.graph.ainvoke(
                        state,
                        config={"configurable": {"thread_id": str(run_id)}},
                    )
            self._add_active_execution_time(result, time.perf_counter() - started, state)
            result["llm_usage"] = collector.summary().model_dump(mode="json")
            response = await self._to_response(run_id, session_id, result)
            await self._persist_response(response, result)
            return response
        except asyncio.CancelledError:
            return await self._handle_cancelled(run_id, session_id, state)
        except Exception as exc:
            latest_state = await self._latest_checkpoint_state(run_id, state)
            self._add_active_execution_time(
                latest_state, time.perf_counter() - started, state
            )
            latest_state["llm_usage"] = collector.summary().model_dump(mode="json")
            return await self._handle_failure(run_id, session_id, latest_state, exc)
        finally:
            reset_llm_usage_collection(usage_token)

    async def stream_start_run(
        self,
        *,
        principal: UserPrincipal,
        session_id: UUID,
        question: str,
        idempotency_key: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        run_id, state, lease_owner, replay = await self._prepare_run(
            principal, session_id, question, idempotency_key=idempotency_key
        )
        if replay:
            response = await self.get_run(principal, run_id)
            response.idempotent_replay = True
            yield {
                "type": "run_reused",
                "data": {"run_id": str(run_id), "session_id": str(session_id)},
            }
            yield {"type": "completed", "data": response.model_dump(mode="json")}
            return
        yield {
            "type": "run_started",
            "data": {"run_id": str(run_id), "session_id": str(session_id)},
        }
        async for event in self._stream_graph_execution(
            run_id=run_id,
            session_id=session_id,
            initial_input=state,
            fallback_state=state,
            lease_owner=lease_owner,
        ):
            yield event

    async def resume_run(
        self,
        *,
        principal: UserPrincipal,
        run_id: UUID,
        feedback: HumanFeedbackRequest,
        idempotency_key: str | None = None,
    ) -> RunResponse:
        run, session_id, lease_owner, replay = await self._prepare_resume(
            principal,
            run_id,
            feedback,
            idempotency_key=idempotency_key or feedback.idempotency_key,
        )
        if replay:
            response = await self.get_run(principal, run_id)
            response.idempotent_replay = True
            return response
        previous_state = dict(run.get("state") or {})
        previous_state["_lease_owner"] = lease_owner
        collector, usage_token = activate_llm_usage_collection(
            previous_state.get("llm_usage"),
            max_total_tokens=self.max_llm_tokens,
        )
        started = time.perf_counter()
        try:
            async with self.execution_coordinator.execution(run_id, lease_owner):
                async with asyncio.timeout(
                    self._remaining_active_execution_seconds(previous_state)
                ):
                    result = await self.graph.ainvoke(
                        Command(
                            resume={
                                "decision": feedback.decision.value,
                                "comment": feedback.comment,
                            }
                        ),
                        config={"configurable": {"thread_id": str(run_id)}},
                    )
            self._add_active_execution_time(result, time.perf_counter() - started, previous_state)
            result["_lease_owner"] = lease_owner
            result["llm_usage"] = collector.summary().model_dump(mode="json")
            response = await self._to_response(run_id, session_id, result)
            await self._persist_response(response, result)
            return response
        except asyncio.CancelledError:
            return await self._handle_cancelled(run_id, session_id, previous_state)
        except Exception as exc:
            latest_state = await self._latest_checkpoint_state(run_id, previous_state)
            self._add_active_execution_time(
                latest_state, time.perf_counter() - started, previous_state
            )
            latest_state["llm_usage"] = collector.summary().model_dump(mode="json")
            return await self._handle_failure(run_id, session_id, latest_state, exc)
        finally:
            reset_llm_usage_collection(usage_token)

    async def stream_resume_run(
        self,
        *,
        principal: UserPrincipal,
        run_id: UUID,
        feedback: HumanFeedbackRequest,
        idempotency_key: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        run, session_id, lease_owner, replay = await self._prepare_resume(
            principal,
            run_id,
            feedback,
            idempotency_key=idempotency_key or feedback.idempotency_key,
        )
        if replay:
            response = await self.get_run(principal, run_id)
            response.idempotent_replay = True
            yield {"type": "run_reused", "data": {"run_id": str(run_id)}}
            yield {"type": "completed", "data": response.model_dump(mode="json")}
            return
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
        fallback = dict(run.get("state") or {})
        fallback["_lease_owner"] = lease_owner
        async for event in self._stream_graph_execution(
            run_id=run_id,
            session_id=session_id,
            initial_input=command,
            fallback_state=fallback,
            lease_owner=lease_owner,
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
            cached_response["run_version"] = run.get("version")
            if run["status"] != RunStatus.AWAITING_APPROVAL.value:
                cached_response["review"] = None
            return RunResponse.model_validate(cached_response)
        if run["status"] == RunStatus.RUNNING.value and not state:
            return RunResponse(
                run_id=run_id,
                session_id=UUID(str(run["session_id"])),
                status=RunStatus.RUNNING,
                run_version=run.get("version"),
            )
        response = await self._to_response(run_id, UUID(str(run["session_id"])), state)
        response.run_version = run.get("version")
        return response

    async def _prepare_run(
        self,
        principal: UserPrincipal,
        session_id: UUID,
        question: str,
        *,
        idempotency_key: str | None,
    ) -> tuple[UUID, dict[str, Any], str, bool]:
        if self.graph is None:
            raise RuntimeError("Workflow service is not started")
        await self.sessions.assert_owner(session_id, principal.user_id)
        lease_owner = str(uuid4())
        created = await self.runs.create_or_get(
            session_id,
            principal.user_id,
            question,
            idempotency_key=idempotency_key,
            lease_owner=lease_owner,
            lease_seconds=self.execution_coordinator.lease_seconds,
            max_concurrent_runs_per_user=self.max_concurrent_runs_per_user,
        )
        if not created.created:
            return created.run_id, dict(created.row.get("state") or {}), lease_owner, True

        history = await self.sessions.get_history(session_id)
        memory = await self.memories.get(session_id, principal.user_id)
        await self.sessions.add_message(
            session_id,
            "user",
            question,
            metadata={
                "message_type": "user_question",
                "run_id": str(created.run_id),
                "idempotency_key": idempotency_key,
            },
        )
        await self.sessions.auto_title_from_question(session_id, question)
        state: dict[str, Any] = {
            "run_id": str(created.run_id),
            "session_id": str(session_id),
            "user_id": str(principal.user_id),
            "question": question,
            "resolved_question": question,
            "conversation_history": history,
            "conversation_memory": memory.model_dump(mode="json"),
            "repair_attempts": 0,
            "status": "running",
            "_lease_owner": lease_owner,
        }
        return created.run_id, state, lease_owner, False

    async def _prepare_resume(
        self,
        principal: UserPrincipal,
        run_id: UUID,
        feedback: HumanFeedbackRequest,
        *,
        idempotency_key: str | None,
    ) -> tuple[dict[str, Any], UUID, str, bool]:
        if self.graph is None:
            raise RuntimeError("Workflow service is not started")
        lease_owner = str(uuid4())
        claim = await self.runs.claim_resume(
            run_id=run_id,
            user_id=principal.user_id,
            decision=feedback.decision.value,
            comment=feedback.comment,
            idempotency_key=idempotency_key,
            lease_owner=lease_owner,
            lease_seconds=self.execution_coordinator.lease_seconds,
            max_concurrent_runs_per_user=self.max_concurrent_runs_per_user,
        )
        run = claim.row
        session_id = UUID(str(run["session_id"]))
        if claim.replay:
            return run, session_id, lease_owner, True
        await self.sessions.add_message(
            session_id,
            "user",
            feedback_content(feedback),
            metadata={
                "message_type": "human_feedback",
                "run_id": str(run_id),
                "decision": feedback.decision.value,
                "comment": feedback.comment,
                "idempotency_key": idempotency_key,
                "exclude_from_context": True,
            },
        )
        return run, session_id, lease_owner, False

    async def _stream_graph_execution(
        self,
        *,
        run_id: UUID,
        session_id: UUID,
        initial_input: Any,
        fallback_state: dict[str, Any],
        lease_owner: str,
    ) -> AsyncIterator[dict[str, Any]]:
        if self.graph is None:
            raise RuntimeError("Workflow service is not started")
        config = {"configurable": {"thread_id": str(run_id)}}
        interrupts: list[Any] = []
        fallback_state["_lease_owner"] = lease_owner
        collector, usage_token = activate_llm_usage_collection(
            fallback_state.get("llm_usage"),
            max_total_tokens=self.max_llm_tokens,
        )
        last_usage_call_count = collector.summary().call_count
        started = time.perf_counter()
        try:
            async with self.execution_coordinator.execution(run_id, lease_owner):
                async with asyncio.timeout(
                    self._remaining_active_execution_seconds(fallback_state)
                ):
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
            self._add_active_execution_time(result, time.perf_counter() - started, fallback_state)
            if not interrupts:
                interrupts = self._snapshot_interrupts(snapshot)
            if interrupts:
                result["__interrupt__"] = interrupts
            result["_lease_owner"] = lease_owner
            result["llm_usage"] = collector.summary().model_dump(mode="json")

            response = await self._to_response(run_id, session_id, result)
            await self._persist_response(response, result)

            if response.status == RunStatus.AWAITING_APPROVAL:
                yield {"type": "review", "data": response.model_dump(mode="json")}
            elif response.status == RunStatus.FAILED:
                logger.error(
                    "agent_run_failed",
                    run_id=str(run_id),
                    session_id=str(session_id),
                    error=response.error,
                )
                yield {
                    "type": "error",
                    "data": {
                        "message": response.error or "No fue posible completar la solicitud.",
                        "run_id": str(run_id),
                        "status": response.status.value,
                    },
                }
            elif response.answer:
                async for delta in self._answer_deltas(response.answer):
                    yield {"type": "answer_delta", "data": {"delta": delta}}

            yield {"type": "completed", "data": response.model_dump(mode="json")}
        except asyncio.CancelledError:
            response = await self._handle_cancelled(run_id, session_id, fallback_state)
            yield {"type": "cancelled", "data": {"message": "Run cancelled"}}
            yield {"type": "completed", "data": response.model_dump(mode="json")}
        except Exception as exc:
            latest_state = await self._latest_checkpoint_state(run_id, fallback_state)
            self._add_active_execution_time(
                latest_state, time.perf_counter() - started, fallback_state
            )
            latest_state["llm_usage"] = collector.summary().model_dump(mode="json")
            response = await self._handle_failure(run_id, session_id, latest_state, exc)
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
            if update.get("context_resolution"):
                resolution = update["context_resolution"]
                summary["is_follow_up"] = resolution.get("is_follow_up")
                summary["resolved_question"] = resolution.get("resolved_question")
                summary["inherited_fields"] = resolution.get("inherited_fields", [])
                summary["requires_clarification"] = resolution.get(
                    "requires_clarification"
                )
            if update.get("domain"):
                summary["domain"] = update["domain"]
            if update.get("autonomous_plan"):
                plan = update["autonomous_plan"]
                summary["autonomous_tasks"] = len(plan.get("tasks") or [])
                summary["autonomous_objective"] = plan.get("objective")
            if update.get("autonomous_current_task_id"):
                summary["current_task_id"] = update["autonomous_current_task_id"]
            if update.get("autonomous_query_mode"):
                summary["query_mode"] = update["autonomous_query_mode"]
            if update.get("autonomous_supervisor_decision"):
                summary["supervisor_action"] = update["autonomous_supervisor_decision"].get("action")
            if update.get("autonomous_evidence") is not None:
                summary["evidence_count"] = len(update.get("autonomous_evidence") or [])
            if update.get("autonomous_critic_review"):
                summary["critic_ready"] = update["autonomous_critic_review"].get("ready_to_finalize")
            if update.get("selected_examples") is not None:
                summary["example_count"] = len(update.get("selected_examples") or [])
            if update.get("query_result"):
                summary["row_count"] = update["query_result"].get("row_count")
            if update.get("feedback_plan"):
                plan = update["feedback_plan"]
                summary["feedback_strategy"] = plan.get("strategy")
                summary["requested_changes"] = [
                    item.get("change_id") for item in plan.get("changes", [])
                ]
            if update.get("feedback_application"):
                application = update["feedback_application"]
                summary["applied_changes"] = application.get("applied_changes", [])
                summary["deferred_changes"] = application.get("deferred_changes", [])
            if update.get("feedback_compliance"):
                compliance = update["feedback_compliance"]
                summary["feedback_compliant"] = compliance.get("compliant")
                summary["missing_changes"] = compliance.get("missing_changes", [])
                summary["unexpected_changes"] = compliance.get("unexpected_changes", [])
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

    async def _handle_cancelled(
        self,
        run_id: UUID,
        session_id: UUID,
        state: dict[str, Any],
    ) -> RunResponse:
        row = await self.runs.get(run_id)
        explicitly_cancelled = bool(row and row.get("cancel_requested_at"))
        status = RunStatus.CANCELLED if explicitly_cancelled else RunStatus.FAILED
        message = (
            "La ejecución fue cancelada por el usuario."
            if explicitly_cancelled
            else "La ejecución se interrumpió porque se perdió el lease o la conexión."
        )
        cancelled_state = dict(state)
        cancelled_state.update({"status": status.value, "error": message})
        response = RunResponse(
            run_id=run_id,
            session_id=session_id,
            status=status,
            answer=message,
            error=None if explicitly_cancelled else message,
            llm_usage=(
                LLMUsageSummary.model_validate(cancelled_state["llm_usage"])
                if cancelled_state.get("llm_usage")
                else None
            ),
            trace=self._build_trace(cancelled_state),
            autonomous_investigation=self._autonomous_summary(cancelled_state),
        )
        try:
            await self._persist_response(response, cancelled_state)
        except RunLeaseError:
            # Another worker owns the run now. Do not overwrite its state after a lost lease.
            logger.warning(
                "cancelled_run_not_persisted_after_lease_loss",
                run_id=str(run_id),
                status=status.value,
            )
        return response

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
            trace=self._build_trace(failed_state),
            autonomous_investigation=self._autonomous_summary(failed_state),
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

    @staticmethod
    def _autonomous_summary(result: dict[str, Any]) -> AutonomousInvestigationSummary | None:
        if not result.get("autonomous_enabled"):
            return None
        plan_payload = result.get("autonomous_plan") or {}
        budget_payload = result.get("autonomous_budget") or {}
        usage_payload = dict(result.get("autonomous_budget_usage") or {})
        usage_payload["iterations"] = int(result.get("autonomous_iteration") or usage_payload.get("iterations") or 0)
        usage_payload["queries_executed"] = int(result.get("autonomous_queries_executed") or usage_payload.get("queries_executed") or 0)
        usage_payload["tasks_created"] = len(plan_payload.get("tasks") or [])
        usage_payload["llm_tokens"] = int((result.get("llm_usage") or {}).get("actual_total_tokens") or usage_payload.get("llm_tokens") or 0)
        return AutonomousInvestigationSummary(
            enabled=True,
            plan=InvestigationPlan.model_validate(plan_payload) if plan_payload else None,
            current_task_id=result.get("autonomous_current_task_id"),
            proposals=[
                SpecialistQueryProposal.model_validate(item)
                for item in result.get("autonomous_proposals") or []
            ],
            evidence=[
                InvestigationEvidence.model_validate(item)
                for item in result.get("autonomous_evidence") or []
            ],
            findings=[
                EvidenceBackedFinding.model_validate(item)
                for item in result.get("autonomous_grounded_findings") or []
            ],
            trajectory=[
                InvestigationTrajectoryEvent.model_validate(item)
                for item in result.get("autonomous_trajectory") or []
            ],
            critic_review=(
                CriticReviewOutput.model_validate(result["autonomous_critic_review"])
                if result.get("autonomous_critic_review")
                else None
            ),
            supervisor_decision=(
                SupervisorDecision.model_validate(result["autonomous_supervisor_decision"])
                if result.get("autonomous_supervisor_decision")
                else None
            ),
            budget=AutonomousBudget.model_validate(budget_payload) if budget_payload else None,
            budget_usage=AutonomousBudgetUsage.model_validate(usage_payload),
        )

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
                resolved_question=(
                    result.get("resolved_question") or payload.get("resolved_question")
                ),
                context_resolution=(
                    ContextResolutionOutput.model_validate(result["context_resolution"])
                    if result.get("context_resolution")
                    else None
                ),
                memory_revision=(result.get("conversation_memory") or {}).get("revision"),
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
                feedback_plan=(
                    SqlFeedbackPlan.model_validate(result["feedback_plan"])
                    if result.get("feedback_plan")
                    else None
                ),
                feedback_application=(
                    SqlFeedbackApplication.model_validate(result["feedback_application"])
                    if result.get("feedback_application")
                    else None
                ),
                feedback_compliance=(
                    FeedbackComplianceResult.model_validate(result["feedback_compliance"])
                    if result.get("feedback_compliance")
                    else None
                ),
                autonomous_investigation=(
                    AutonomousInvestigationSummary.model_validate(payload["autonomous_investigation"])
                    if payload.get("autonomous_investigation")
                    else self._autonomous_summary(result)
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
            resolved_question=result.get("resolved_question"),
            context_resolution=(
                ContextResolutionOutput.model_validate(result["context_resolution"])
                if result.get("context_resolution")
                else None
            ),
            memory_revision=(result.get("conversation_memory") or {}).get("revision"),
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
            feedback_plan=(
                SqlFeedbackPlan.model_validate(result["feedback_plan"])
                if result.get("feedback_plan")
                else None
            ),
            feedback_application=(
                SqlFeedbackApplication.model_validate(result["feedback_application"])
                if result.get("feedback_application")
                else None
            ),
            feedback_compliance=(
                FeedbackComplianceResult.model_validate(result["feedback_compliance"])
                if result.get("feedback_compliance")
                else None
            ),
            autonomous_investigation=self._autonomous_summary(result),
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

        resolution = result.get("context_resolution") or {}
        if resolution:
            add(
                "resolve_context",
                "Resolución de contexto",
                (
                    "La pregunta se convirtió en una solicitud autocontenida cuando "
                    "dependía del turno anterior."
                ),
                {
                    "is_follow_up": resolution.get("is_follow_up"),
                    "resolved_question": resolution.get("resolved_question"),
                    "inherited_fields": resolution.get("inherited_fields", []),
                    "confidence": resolution.get("confidence"),
                },
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

        autonomous_plan = result.get("autonomous_plan") or {}
        if autonomous_plan:
            add(
                "plan_investigation",
                "Plan autónomo gobernado",
                "El planificador definió tareas de evidencia dentro de especialistas y presupuestos permitidos.",
                {
                    "objective": autonomous_plan.get("objective"),
                    "strategy": autonomous_plan.get("strategy"),
                    "tasks": [
                        {
                            "task_id": task.get("task_id"),
                            "specialist": task.get("specialist"),
                            "query_mode": task.get("query_mode"),
                            "status": task.get("status"),
                            "dependencies": task.get("dependencies", []),
                        }
                        for task in autonomous_plan.get("tasks", [])
                    ],
                },
            )

        supervisor = result.get("autonomous_supervisor_decision") or {}
        if supervisor:
            add(
                "supervisor_review",
                "Decisión del supervisor",
                "La decisión fue validada por políticas determinísticas antes de delegar o finalizar.",
                {
                    "action": supervisor.get("action"),
                    "next_task_id": supervisor.get("next_task_id"),
                    "new_tasks": [
                        task.get("task_id") for task in supervisor.get("new_tasks", [])
                    ],
                    "rejected_conclusions": supervisor.get("rejected_conclusions", []),
                },
            )

        critic = result.get("autonomous_critic_review") or {}
        if critic:
            add(
                "critic_review",
                "Revisión crítica",
                "El crítico comprobó suficiencia, contradicciones y conclusiones no respaldadas.",
                {
                    "accepted_evidence_ids": critic.get("accepted_evidence_ids", []),
                    "rejected_conclusions": critic.get("rejected_conclusions", []),
                    "contradictions": critic.get("contradictions", []),
                    "missing_evidence": critic.get("missing_evidence", []),
                    "ready_to_finalize": critic.get("ready_to_finalize"),
                },
            )

        autonomous_evidence = result.get("autonomous_evidence") or []
        if autonomous_evidence:
            add(
                "record_evidence",
                "Evidencia de investigación",
                "Cada evidencia conserva tarea, especialista, SQL aprobado y verificación.",
                {
                    "count": len(autonomous_evidence),
                    "evidence": [
                        {
                            "evidence_id": item.get("evidence_id"),
                            "task_id": item.get("task_id"),
                            "specialist": item.get("specialist"),
                            "domain": item.get("domain"),
                        }
                        for item in autonomous_evidence
                    ],
                },
            )

        budget = result.get("autonomous_budget") or {}
        budget_usage = result.get("autonomous_budget_usage") or {}
        if budget:
            add(
                "autonomous_budget",
                "Presupuestos de la investigación",
                "Los límites se fijaron al inicio y no pueden ser modificados por los agentes.",
                {"budget": budget, "usage": budget_usage},
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

        feedback_plan = result.get("feedback_plan") or {}
        feedback_compliance = result.get("feedback_compliance") or {}
        if feedback_plan:
            add(
                "interpret_feedback",
                "Plan de corrección semántica",
                "El feedback se descompuso en cambios tipados antes de regenerar el SQL.",
                {
                    "strategy": feedback_plan.get("strategy"),
                    "summary": feedback_plan.get("summary"),
                    "changes": [
                        {
                            "id": item.get("change_id"),
                            "type": item.get("change_type"),
                            "target": item.get("target"),
                        }
                        for item in feedback_plan.get("changes", [])
                    ],
                },
            )
        if feedback_compliance:
            add(
                "validate_feedback_compliance",
                "Cumplimiento del feedback",
                "Se comparó la revisión con cada cambio solicitado y con el contrato anterior.",
                {
                    "compliant": feedback_compliance.get("compliant"),
                    "applied_changes": feedback_compliance.get("applied_changes", []),
                    "missing_changes": feedback_compliance.get("missing_changes", []),
                    "unexpected_changes": feedback_compliance.get("unexpected_changes", []),
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
        current_memory = ConversationMemory.model_validate(
            state.get("conversation_memory") or {}
        )
        merged_memory = self.memory_service.merge(current_memory, state, response)
        current_payload = current_memory.model_dump(
            mode="json", exclude={"revision", "updated_at"}
        )
        merged_payload = merged_memory.model_dump(
            mode="json", exclude={"revision", "updated_at"}
        )
        if merged_payload != current_payload:
            stored_memory = await self.memories.upsert(
                response.session_id,
                UUID(str(state["user_id"])),
                merged_memory,
                response.run_id,
            )
        else:
            stored_memory = current_memory
        state["conversation_memory"] = stored_memory.model_dump(mode="json")
        response.memory_revision = stored_memory.revision
        response_payload = response.model_dump(mode="json")
        state["_api_response"] = response_payload
        version = await self.runs.update(
            response.run_id,
            response.status.value,
            state=state,
            error=response.error,
            lease_owner=state.get("_lease_owner"),
        )
        response.run_version = version
        response_payload = response.model_dump(mode="json")

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
            RunStatus.CANCELLED,
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
