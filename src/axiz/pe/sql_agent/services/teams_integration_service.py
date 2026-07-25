from __future__ import annotations

from uuid import UUID

from axiz.pe.sql_agent.core.redis_client import RedisStore
from axiz.pe.sql_agent.models.contracts import (
    ApprovalDecision,
    HumanFeedbackRequest,
    RunResponse,
    RunStatus,
    TeamsMessageRequest,
    TeamsMessageResponse,
    UserPrincipal,
)
from axiz.pe.sql_agent.repositories.session_repository import SessionRepository
from axiz.pe.sql_agent.repositories.user_repository import UserRepository
from axiz.pe.sql_agent.workflow.service import AgentWorkflowService


class TeamsIntegrationService:
    def __init__(
        self,
        *,
        users: UserRepository,
        sessions: SessionRepository,
        workflow: AgentWorkflowService,
        redis: RedisStore,
    ) -> None:
        self.users = users
        self.sessions = sessions
        self.workflow = workflow
        self.redis = redis

    async def handle(self, message: TeamsMessageRequest) -> TeamsMessageResponse:
        username = f"teams:{message.channel_user_id}"
        user_id = await self.users.get_or_create_external_user(
            external_id=message.channel_user_id,
            username=username,
            auth_source="teams",
            roles=["analyst"],
        )
        principal = UserPrincipal(
            user_id=user_id,
            username=username,
            roles=["analyst"],
            auth_source="teams",
        )
        session_id = await self.sessions.get_or_create_channel_session(
            user_id=user_id,
            channel="teams",
            conversation_id=message.conversation_id,
        )
        pending_key = self._pending_key(message)
        pending = await self.redis.get_json(pending_key)
        parsed_feedback = self._parse_feedback(message.text) if pending else None

        if pending and parsed_feedback:
            response = await self.workflow.resume_run(
                principal=principal,
                run_id=UUID(str(pending["run_id"])),
                feedback=parsed_feedback,
            )
        else:
            if pending:
                return TeamsMessageResponse(
                    text=(
                        "Hay una consulta esperando revisión. Responde `approve`, `reject` o "
                        "`change: <comentario>` antes de iniciar otra pregunta."
                    ),
                    awaiting_approval=True,
                    run_id=UUID(str(pending["run_id"])),
                )
            response = await self.workflow.start_run(
                principal=principal,
                session_id=session_id,
                question=message.text,
            )

        if response.status == RunStatus.AWAITING_APPROVAL and response.review:
            await self.redis.set_json(
                pending_key,
                {"run_id": str(response.run_id)},
                ttl_seconds=24 * 3600,
            )
            return TeamsMessageResponse(
                text=self._format_review(response),
                awaiting_approval=True,
                run_id=response.run_id,
            )

        await self.redis.delete(pending_key)
        return TeamsMessageResponse(
            text=self._format_final(response),
            awaiting_approval=False,
            run_id=response.run_id,
        )

    @staticmethod
    def _parse_feedback(text: str) -> HumanFeedbackRequest | None:
        normalized = text.strip()
        lowered = normalized.lower()
        if lowered in {"approve", "aprobar", "aprobado"}:
            return HumanFeedbackRequest(decision=ApprovalDecision.APPROVE)
        if lowered in {"reject", "rechazar", "rechazado"}:
            return HumanFeedbackRequest(decision=ApprovalDecision.REJECT)
        prefixes = ("change:", "cambiar:", "corregir:")
        for prefix in prefixes:
            if lowered.startswith(prefix):
                comment = normalized[len(prefix) :].strip()
                return HumanFeedbackRequest(
                    decision=ApprovalDecision.REQUEST_CHANGES,
                    comment=comment or "Revise the query",
                )
        return None

    @staticmethod
    def _pending_key(message: TeamsMessageRequest) -> str:
        return f"teams:pending:{message.conversation_id}:{message.channel_user_id}"

    @staticmethod
    def _format_review(response: RunResponse) -> str:
        review = response.review
        assert review is not None
        assumptions = "\n".join(f"- {item}" for item in review.assumptions) or "- Ninguna"
        return (
            f"**Interpretación:** {review.interpretation}\n\n"
            f"**Supuestos:**\n{assumptions}\n\n"
            f"**SQL propuesto:**\n```sql\n{review.sql}\n```\n\n"
            "Responde `approve`, `reject` o `change: <comentario>`."
        )

    @staticmethod
    def _format_final(response: RunResponse) -> str:
        if response.error:
            return f"La ejecución terminó con error: {response.error}"
        findings = "\n".join(f"- {item}" for item in response.key_findings)
        caveats = "\n".join(f"- {item}" for item in response.caveats)
        text = response.answer or f"Estado: {response.status.value}"
        if findings:
            text += f"\n\n**Hallazgos:**\n{findings}"
        if caveats:
            text += f"\n\n**Advertencias:**\n{caveats}"
        return text
