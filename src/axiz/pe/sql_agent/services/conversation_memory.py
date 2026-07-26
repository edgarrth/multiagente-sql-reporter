from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from axiz.pe.sql_agent.models.contracts import (
    ConversationMemory,
    QueryFilter,
    RunResponse,
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
        if state.get("intent") != "analytical_query":
            return current
        if not state.get("interpretation") and not state.get("generated_sql"):
            return current

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
        sql_filters, sql_window = self.sql_memory_extractor.extract(
            state.get("generated_sql")
        )
        ordering, limit_value, source_objects = (
            self.sql_memory_extractor.extract_query_contract(state.get("generated_sql"))
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

        # A new analytical request must not accidentally carry the prior result as if it
        # belonged to the newly proposed SQL. Result fields are repopulated after execution.
        result_schema = list(result.columns) if result else []
        result_sample = (
            list(result.rows[: self.result_sample_rows]) if result else []
        )
        row_count = result.row_count if result else None

        return ConversationMemory(
            schema_version=max(3, current.schema_version),
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
            last_sql=state.get("generated_sql"),
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
            updated_at=datetime.now(UTC),
        )
