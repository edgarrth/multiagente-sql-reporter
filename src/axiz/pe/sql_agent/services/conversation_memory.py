from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from axiz.pe.sql_agent.models.contracts import (
    ConversationMemory,
    QueryFilter,
    RunResponse,
    RunStatus,
    TimeWindowContext,
)
from axiz.pe.sql_agent.tools.sql_memory_extractor import SqlMemoryExtractor


class StructuredConversationMemoryService:
    """Builds a bounded analytical-memory snapshot from a workflow state."""

    def __init__(
        self,
        result_sample_rows: int = 5,
        sql_dialect: str = "postgres",
    ) -> None:
        self.result_sample_rows = max(0, result_sample_rows)
        self.sql_memory_extractor = SqlMemoryExtractor(sql_dialect)

    def merge(
        self,
        current: ConversationMemory,
        state: dict[str, Any],
        response: RunResponse,
    ) -> ConversationMemory:
        """Merge a run without allowing failed revisions to erase the last valid SQL baseline.

        Conversation memory distinguishes the latest *attempt* from the latest usable analytical
        contract. Only a run that produced SQL and reached review/completion may replace the
        baseline. Failed, cancelled, rejected or clarification-only turns preserve the previous
        contract and record their pending revision separately.
        """
        attempt_update = {
            "schema_version": max(5, current.schema_version),
            "last_attempt_run_id": UUID(str(response.run_id)),
            "last_attempt_status": response.status.value,
            "last_attempt_user_request": str(state.get("question") or "") or None,
            "last_attempt_error": response.error,
            "updated_at": datetime.now(UTC),
        }
        if state.get("intent") != "analytical_query":
            return current.model_copy(update=attempt_update)

        candidate_sql = str(state.get("generated_sql") or "").strip()
        baseline_statuses = {RunStatus.AWAITING_APPROVAL, RunStatus.COMPLETED}
        security_approved = bool(
            response.security_validation and response.security_validation.approved
        )
        cost_approved = bool(
            response.cost_validation and response.cost_validation.approved
        )
        can_replace_baseline = (
            bool(candidate_sql)
            and response.status in baseline_statuses
            and security_approved
            and cost_approved
        )
        if not can_replace_baseline:
            context_relation = str(
                (state.get("context_resolution") or {}).get("relation") or ""
            )
            is_revision = bool(state.get("follow_up_change_plan")) or (
                context_relation == "analytical_follow_up"
            )
            pending_plan = dict(state.get("feedback_plan") or {}) if is_revision else {}
            pending_feedback = (
                str(
                    state.get("feedback_comment")
                    or state.get("question")
                    or ""
                ).strip()
                if is_revision
                else None
            )
            return current.model_copy(
                update={
                    **attempt_update,
                    "pending_revision_feedback": pending_feedback or None,
                    "pending_revision_plan": pending_plan,
                }
            )

        result = response.result
        usage = response.llm_usage
        models: list[str] = []
        if usage:
            for call in usage.calls:
                if call.model and call.model not in models:
                    models.append(call.model)

        declared_filters = [
            QueryFilter.model_validate(item)
            for item in (state.get("selected_filters") or [])
        ]
        sql_filters, sql_window = self.sql_memory_extractor.extract(candidate_sql)
        ordering, limit_value, source_objects = (
            self.sql_memory_extractor.extract_query_contract(candidate_sql)
        )
        filters: list[QueryFilter] = []
        seen_filters: set[tuple[str, str, str]] = set()
        for item in [*declared_filters, *sql_filters]:
            key = (item.field.lower(), item.operator.upper(), item.value)
            if key in seen_filters:
                continue
            seen_filters.add(key)
            filters.append(item)

        raw_window = state.get("time_window")
        time_window = (
            TimeWindowContext.model_validate(raw_window) if raw_window else sql_window
        )
        if time_window and sql_window:
            time_window = time_window.model_copy(
                update={
                    "start_expression": (
                        time_window.start_expression or sql_window.start_expression
                    ),
                    "end_expression": (
                        time_window.end_expression or sql_window.end_expression
                    ),
                }
            )

        result_schema = list(result.columns) if result else []
        result_sample = (
            list(result.rows[: self.result_sample_rows]) if result else []
        )
        row_count = result.row_count if result else None

        return ConversationMemory(
            schema_version=max(5, current.schema_version),
            revision=current.revision,
            last_run_id=UUID(str(response.run_id)),
            last_status=response.status.value,
            last_user_request=str(state.get("question") or "") or None,
            last_resolved_question=str(
                state.get("resolved_question") or state.get("question") or ""
            )
            or None,
            last_interpretation=state.get("interpretation"),
            last_domain=state.get("domain"),
            last_metrics=list(state.get("selected_metrics") or []),
            last_dimensions=list(state.get("selected_dimensions") or []),
            last_filters=filters,
            last_time_window=time_window,
            last_ordering=ordering,
            last_limit=limit_value,
            last_source_objects=(
                list(state.get("source_objects") or []) or source_objects
            ),
            last_sql=candidate_sql,
            last_result_schema=result_schema,
            last_result_sample=result_sample,
            last_row_count=row_count,
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
            pending_revision_plan={},
            last_query_spec=(state.get("query_spec") or None),
            last_compiled_sql_artifact=(state.get("compiled_sql_artifact") or None),
            updated_at=datetime.now(UTC),
        )

    def restore_from_payload(
        self,
        current: ConversationMemory,
        payload: dict[str, Any] | None,
    ) -> ConversationMemory:
        """Recover the last persisted SQL proposal for sessions created by older versions.

        This is a compatibility path for memories that were previously overwritten by a failed
        follow-up. The assistant message payload is already persisted in the same session and is
        therefore a trusted local source; no LLM or textual SQL scraping is involved.
        """
        payload = dict(payload or {})
        sql = str(payload.get("sql") or "").strip()
        if not sql:
            return current
        filters, window = self.sql_memory_extractor.extract(sql)
        ordering, limit_value, sources = self.sql_memory_extractor.extract_query_contract(sql)
        run_id = payload.get("run_id")
        try:
            restored_run_id = UUID(str(run_id)) if run_id else current.last_run_id
        except (TypeError, ValueError):
            restored_run_id = current.last_run_id
        status = str(payload.get("status") or current.last_status or "completed")
        return current.model_copy(
            update={
                "schema_version": max(5, current.schema_version),
                "last_run_id": restored_run_id,
                "last_status": status,
                "last_resolved_question": (
                    payload.get("resolved_question")
                    or current.last_resolved_question
                    or current.last_user_request
                ),
                "last_interpretation": (
                    payload.get("interpretation") or current.last_interpretation
                ),
                "last_domain": payload.get("domain") or current.last_domain,
                "last_filters": filters or current.last_filters,
                "last_time_window": window or current.last_time_window,
                "last_ordering": ordering or current.last_ordering,
                "last_limit": limit_value if limit_value is not None else current.last_limit,
                "last_source_objects": sources or current.last_source_objects,
                "last_sql": sql,
                "last_query_spec": payload.get("query_spec") or current.last_query_spec,
                "last_compiled_sql_artifact": (
                    payload.get("compiled_sql_artifact")
                    or current.last_compiled_sql_artifact
                ),
                "updated_at": datetime.now(UTC),
            }
        )

