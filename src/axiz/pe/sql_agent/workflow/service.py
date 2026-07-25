from __future__ import annotations

from typing import Any

import structlog
from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from axiz.pe.sql_agent.models.contracts import (
    HumanFeedbackRequest,
    QueryResult,
    ReviewPayload,
    RunResponse,
    RunStatus,
    UserPrincipal,
    VisualizationSpec,
)
from axiz.pe.sql_agent.repositories.run_repository import RunRepository
from axiz.pe.sql_agent.repositories.session_repository import SessionRepository


logger = structlog.get_logger(__name__)


class AgentWorkflowService:
    def __init__(
        self,
        *,
        checkpoint_dsn: str,
        graph_builder,
        sessions: SessionRepository,
        runs: RunRepository,
    ) -> None:
        self.checkpoint_dsn = checkpoint_dsn
        self.graph_builder = graph_builder
        self.sessions = sessions
        self.runs = runs
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
        if self.graph is None:
            raise RuntimeError("Workflow service is not started")
        await self.sessions.assert_owner(session_id, principal.user_id)
        history = await self.sessions.get_history(session_id)
        await self.sessions.add_message(session_id, "user", question)
        run_id = await self.runs.create(session_id, principal.user_id, question)
        state = {
            "run_id": str(run_id),
            "session_id": str(session_id),
            "user_id": str(principal.user_id),
            "question": question,
            "conversation_history": history,
            "repair_attempts": 0,
            "status": "running",
        }
        try:
            result = await self.graph.ainvoke(
                state,
                config={"configurable": {"thread_id": str(run_id)}},
            )
            response = await self._to_response(run_id, session_id, result)
            await self._persist_response(response, result)
            return response
        except Exception as exc:
            failed_state = dict(state)
            failed_state.update({"status": "failed", "error": str(exc)})
            try:
                await self.runs.update(run_id, "failed", state=failed_state, error=str(exc))
            except Exception as persistence_exc:
                logger.exception(
                    "failed_to_persist_agent_error",
                    run_id=str(run_id),
                    original_error=str(exc),
                    persistence_error=str(persistence_exc),
                )
            return RunResponse(
                run_id=run_id,
                session_id=session_id,
                status=RunStatus.FAILED,
                error=str(exc),
            )

    async def resume_run(
        self,
        *,
        principal: UserPrincipal,
        run_id: UUID,
        feedback: HumanFeedbackRequest,
    ) -> RunResponse:
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
            response = await self._to_response(run_id, session_id, result)
            await self._persist_response(response, result)
            return response
        except Exception as exc:
            try:
                await self.runs.update(run_id, "failed", error=str(exc))
            except Exception as persistence_exc:
                logger.exception(
                    "failed_to_persist_agent_error",
                    run_id=str(run_id),
                    original_error=str(exc),
                    persistence_error=str(persistence_exc),
                )
            return RunResponse(
                run_id=run_id,
                session_id=session_id,
                status=RunStatus.FAILED,
                error=str(exc),
            )

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
                sql=payload.get("sql"),
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
        return RunResponse(
            run_id=run_id,
            session_id=session_id,
            status=status,
            answer=result.get("answer"),
            key_findings=result.get("key_findings", []),
            caveats=result.get("caveats", []),
            result=query_result,
            visualization=visualization,
            sql=result.get("generated_sql"),
            error=result.get("error"),
        )

    async def _persist_response(self, response: RunResponse, state: dict[str, Any]) -> None:
        state = dict(state)
        state["_api_response"] = response.model_dump(mode="json")
        await self.runs.update(
            response.run_id,
            response.status.value,
            state=state,
            error=response.error,
        )
        if response.status in {
            RunStatus.COMPLETED,
            RunStatus.REJECTED,
            RunStatus.NEEDS_CLARIFICATION,
        } and response.answer:
            await self.sessions.add_message(
                response.session_id,
                "assistant",
                response.answer,
                metadata={
                    "run_id": str(response.run_id),
                    "sql": response.sql,
                    "status": response.status.value,
                },
            )
