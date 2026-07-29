from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from axiz.pe.sql_agent.models.contracts import ConversationMemory, RunResponse, RunStatus
from axiz.pe.sql_agent.models.sql_artifacts import CompiledSqlArtifact
from axiz.pe.sql_agent.services.sql_artifacts import SqlArtifactService


class StructuredConversationMemoryService:
    """Persist a bounded, generic baseline for future autonomous SQL turns."""

    def __init__(self, result_sample_rows: int = 5, sql_dialect: str = "postgres") -> None:
        self.result_sample_rows = max(0, result_sample_rows)
        self.sql_artifacts = SqlArtifactService(sql_dialect)

    def merge(
        self,
        current: ConversationMemory,
        state: dict[str, Any],
        response: RunResponse,
    ) -> ConversationMemory:
        attempt = {
            "schema_version": max(6, current.schema_version),
            "last_attempt_run_id": UUID(str(response.run_id)),
            "last_attempt_status": response.status.value,
            "last_attempt_user_request": str(state.get("question") or "") or None,
            "last_attempt_error": response.error,
            "updated_at": datetime.now(UTC),
        }
        if state.get("intent") != "analytical_query":
            return current.model_copy(update=attempt)

        sql = str(state.get("generated_sql") or "").strip()
        can_replace = (
            bool(sql)
            and response.status in {RunStatus.AWAITING_APPROVAL, RunStatus.COMPLETED}
            and bool(response.security_validation and response.security_validation.approved)
            and bool(response.cost_validation and response.cost_validation.approved)
        )
        if not can_replace:
            is_revision = bool(state.get("revision_requested") or state.get("follow_up_change_plan"))
            return current.model_copy(
                update={
                    **attempt,
                    "pending_revision_feedback": (
                        str(state.get("feedback_comment") or state.get("question") or "").strip()
                        or None
                    ) if is_revision else None,
                }
            )

        artifact_payload = dict(state.get("compiled_sql_artifact") or {})
        if artifact_payload:
            artifact = CompiledSqlArtifact.model_validate(artifact_payload)
        else:
            artifact = self.sql_artifacts.compile(sql)
        result = response.result
        usage = response.llm_usage
        models: list[str] = []
        if usage:
            for call in usage.calls:
                if call.model and call.model not in models:
                    models.append(call.model)

        return ConversationMemory(
            schema_version=max(6, current.schema_version),
            revision=current.revision,
            last_run_id=UUID(str(response.run_id)),
            last_status=response.status.value,
            last_user_request=str(state.get("question") or "") or None,
            last_resolved_question=str(
                state.get("resolved_question") or state.get("question") or ""
            ) or None,
            last_interpretation=state.get("interpretation"),
            last_domain=state.get("domain"),
            last_sql=sql,
            last_sql_snapshot=artifact.snapshot,
            last_source_objects=list(artifact.snapshot.sources),
            last_result_schema=list(result.columns) if result else [],
            last_result_sample=list(result.rows[: self.result_sample_rows]) if result else [],
            last_row_count=result.row_count if result else None,
            last_answer=response.answer if result else None,
            last_key_findings=list(response.key_findings if result else []),
            last_models=models,
            last_token_usage=usage.actual_total_tokens if usage else None,
            last_investigation=(
                response.autonomous_investigation.model_dump(mode="json")
                if response.autonomous_investigation
                else {}
            ),
            last_attempt_run_id=UUID(str(response.run_id)),
            last_attempt_status=response.status.value,
            last_attempt_user_request=str(state.get("question") or "") or None,
            last_attempt_error=response.error,
            pending_revision_feedback=None,
            last_compiled_sql_artifact=artifact,
            updated_at=datetime.now(UTC),
        )

    def restore_from_payload(
        self,
        current: ConversationMemory,
        payload: dict[str, Any] | None,
    ) -> ConversationMemory:
        payload = dict(payload or {})
        sql = str(payload.get("sql") or "").strip()
        if not sql:
            return current
        try:
            artifact = (
                CompiledSqlArtifact.model_validate(payload["compiled_sql_artifact"])
                if payload.get("compiled_sql_artifact")
                else self.sql_artifacts.compile(sql)
            )
        except Exception:
            return current
        run_id = payload.get("run_id")
        try:
            restored_run_id = UUID(str(run_id)) if run_id else current.last_run_id
        except (TypeError, ValueError):
            restored_run_id = current.last_run_id
        return current.model_copy(
            update={
                "schema_version": max(6, current.schema_version),
                "last_run_id": restored_run_id,
                "last_status": str(payload.get("status") or current.last_status or "completed"),
                "last_resolved_question": payload.get("resolved_question")
                or current.last_resolved_question,
                "last_interpretation": payload.get("interpretation")
                or current.last_interpretation,
                "last_domain": payload.get("domain") or current.last_domain,
                "last_sql": sql,
                "last_sql_snapshot": artifact.snapshot,
                "last_source_objects": list(artifact.snapshot.sources),
                "last_compiled_sql_artifact": artifact,
                "updated_at": datetime.now(UTC),
            }
        )
