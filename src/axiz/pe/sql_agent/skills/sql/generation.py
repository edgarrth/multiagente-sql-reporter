from __future__ import annotations

import json
from typing import Any

from axiz.pe.sql_agent.models.contracts import SqlGenerationOutput
from axiz.pe.sql_agent.services.llm import StructuredLLM
from axiz.pe.sql_agent.tools.sql_ast_analyzer import SqlAstAnalyzer



class SqlGenerationSkill:
    """Generate SQL and repair rejected candidates with different context budgets.

    Initial generation may need catalog examples and conversation context. A repair already has a
    concrete SQL candidate and deterministic validator feedback, so replaying the complete prompt is
    both expensive and harmful. Repairs therefore use a dedicated, compact prompt and model profile.
    """

    def __init__(
        self,
        llm: StructuredLLM,
        dialect: str,
        max_result_rows: int,
        *,
        repair_llm: StructuredLLM | None = None,
        revision_llm: StructuredLLM | None = None,
    ) -> None:
        self.llm = llm
        self.repair_llm = repair_llm or llm
        self.revision_llm = revision_llm or llm
        self.dialect = dialect
        self.max_result_rows = max_result_rows

    @staticmethod
    def _without_duplicate_sql(memory: dict[str, Any] | None) -> dict[str, Any]:
        """Keep structured semantics but do not send the previous SQL twice."""
        projected = dict(memory or {})
        projected.pop("last_sql", None)
        return projected

    @staticmethod
    def _extract_sources(sql: str) -> list[str]:
        if not sql or not sql.strip():
            return []
        try:
            analyzer = SqlAstAnalyzer(dialect="postgres")
            return analyzer.sources(analyzer.parse(sql))
        except Exception:
            return []

    @classmethod
    def _repair_context(
        cls,
        semantic_context: dict[str, Any],
        *,
        failed_sql: str,
        current_contract: dict[str, Any] | None,
    ) -> dict[str, Any]:
        contracts = dict(semantic_context.get("source_contracts") or {})
        requested_sources = [
            str(item)
            for item in (current_contract or {}).get("source_objects", [])
            if item
        ]
        requested_sources.extend(cls._extract_sources(failed_sql))

        selected_sources: list[str] = []
        for candidate in requested_sources:
            normalized = candidate.strip().strip('"').lower()
            for source in contracts:
                if source.lower() == normalized and source not in selected_sources:
                    selected_sources.append(source)

        if not selected_sources:
            lowered_sql = (failed_sql or "").lower()
            selected_sources = [
                source for source in contracts if source.lower() in lowered_sql
            ]
        if not selected_sources:
            # The semantic projector already ranked contracts by relevance. Keep at most two as a
            # safe fallback instead of reintroducing the complete domain catalog.
            selected_sources = list(contracts)[:2]

        selected_contracts = {
            source: {
                key: value
                for key, value in dict(contracts[source] or {}).items()
                if key in {"name", "source", "grain", "timezone", "columns", "allowed_values"}
            }
            for source in selected_sources
            if source in contracts
        }
        selected_columns = {
            str(column)
            for contract in selected_contracts.values()
            for column in (contract or {}).get("columns", [])
        }
        contract_payload = current_contract or {}
        selected_names = {
            str(item)
            for key in ("selected_metrics", "selected_dimensions")
            for item in contract_payload.get(key, [])
        }

        symbols = dict(semantic_context.get("semantic_symbols") or {})

        def relevant(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
            return [
                item
                for item in items
                if str(item.get("source") or "") in selected_sources
                or str(item.get("column") or "") in selected_columns
                or str(item.get("name") or "") in selected_names
            ][:limit]

        policy = dict(semantic_context.get("query_policy") or {})
        compact_policy = {
            key: policy[key]
            for key in ("required_filter_columns", "maximum_rows", "timezone")
            if key in policy
        }
        return {
            "allowed_sources": selected_sources,
            "query_policy": compact_policy,
            "source_contracts": selected_contracts,
            "calendar_context": dict(semantic_context.get("calendar_context") or {}),
            "semantic_symbols": {
                "metrics": relevant(list(symbols.get("metrics") or []), 8),
                "dimensions": relevant(list(symbols.get("dimensions") or []), 10),
                "sources": relevant(list(symbols.get("sources") or []), 3),
            },
        }

    @staticmethod
    def _contract_from_memory(memory: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(memory or {})
        return {
            "interpretation": payload.get("last_interpretation") or "",
            "selected_metrics": list(payload.get("last_metrics") or []),
            "selected_dimensions": list(payload.get("last_dimensions") or []),
            "selected_filters": list(payload.get("last_filters") or []),
            "time_window": payload.get("last_time_window"),
            "ordering": list(payload.get("last_ordering") or []),
            "limit": payload.get("last_limit"),
            "source_objects": list(payload.get("last_source_objects") or []),
        }

    @staticmethod
    def _revision_context(semantic_context: dict[str, Any]) -> dict[str, Any]:
        """Drop retrieval prose and examples after feedback has been converted to a typed plan."""
        contracts = {
            source: {
                key: value
                for key, value in dict(contract or {}).items()
                if key in {"name", "source", "grain", "timezone", "columns", "allowed_values"}
            }
            for source, contract in dict(
                semantic_context.get("source_contracts") or {}
            ).items()
        }
        policy = dict(semantic_context.get("query_policy") or {})
        return {
            "allowed_sources": list(semantic_context.get("allowed_sources") or []),
            "query_policy": {
                key: policy[key]
                for key in ("required_filter_columns", "maximum_rows", "timezone")
                if key in policy
            },
            "source_contracts": contracts,
            "calendar_context": dict(semantic_context.get("calendar_context") or {}),
            "semantic_symbols": dict(semantic_context.get("semantic_symbols") or {}),
        }

    async def _revise(
        self,
        *,
        question: str,
        previous_sql: str,
        feedback: str | None,
        feedback_plan: dict[str, Any],
        semantic_context: dict[str, Any],
        current_contract: dict[str, Any],
    ) -> SqlGenerationOutput:
        system = f"""
You are the SQL Engineer of a governed autonomous agent society. Revise the COMPLETE previously
approved {self.dialect} SELECT using the user's complete natural-language feedback. The previous SQL
and the raw feedback are the primary revision contract. Do not require the request to fit a fixed
filter/metric/date/projection vocabulary. You may modify any SQL element the user clearly requests:
SELECT expressions and their order, aliases, filters, joins, grouping, aggregates, HAVING, ORDER BY,
time windows, LIMIT or semantic source.

Rules:
- Return the complete revised SQL, not a patch and not a fragment.
- Apply every explicit request, including compound edits, spelling errors and references to columns
  visible in previous_sql or revision_context.source_contracts.
- Preserve every SQL element that the user did not request to change.
- Reconcile dependencies: removing or renaming a projection must update ORDER BY, GROUP BY, HAVING
  and derived expressions that reference it. Reordering projected columns is a valid requested edit.
- Metadata fields selected_metrics, selected_dimensions, selected_filters, time_window and
  source_objects must describe the FINAL SQL, never the previous SQL.
- Use only exact sources and columns published in revision_context.source_contracts.
- When the request has two genuinely business-valid interpretations that cannot be resolved from
  previous_sql, question and current_contract, set requires_clarification=true, provide one concise
  clarification_question, and return previous_sql unchanged. Otherwise do not ask for clarification.
- Never add DDL, DML, comments, multiple statements, raw tables or unlisted sources. Preserve an
  existing date predicate unless the user asks to change or remove it, but never invent a date range
  that was absent from the baseline. Keep an explicit LIMIT no greater than max_allowed_rows.
- change_summary must briefly list the edits actually made.
Return a complete SqlGenerationOutput without Markdown fences.
""".strip()
        payload = {
            "original_question": question,
            "raw_user_feedback": feedback or feedback_plan.get("raw_user_message") or "",
            "previous_sql": previous_sql,
            "current_contract": current_contract,
            "revision_context": self._revision_context(semantic_context),
            "governance": {
                "read_only": True,
                "single_statement": True,
                "max_allowed_rows": self.max_result_rows,
                "preserve_unrequested_sql": True,
            },
        }
        return await self.revision_llm.parse(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, default=str),
            response_model=SqlGenerationOutput,
        )

    async def _repair(
        self,
        *,
        question: str,
        semantic_context: dict[str, Any],
        prior_compliance: dict[str, Any],
        current_contract: dict[str, Any] | None,
        feedback_plan: dict[str, Any] | None,
    ) -> SqlGenerationOutput:
        failed_sql = str(prior_compliance.get("failed_sql") or "").strip()
        retry_instruction = str(
            prior_compliance.get("retry_instruction") or ""
        ).strip()
        repair_context = self._repair_context(
            semantic_context,
            failed_sql=failed_sql,
            current_contract=current_contract,
        )
        system = f"""
You repair one governed {self.dialect} SELECT statement rejected by deterministic validation.
Preserve the requested business result, selected metrics, dimensions, filters, grouping, ordering,
time window and semantic sources unless the validator explicitly identifies one of them as invalid.
Use only the exact sources and columns in repair_context.source_contracts; these are the
exact available identifiers. Do not fabricate columns. Treat the validator feedback as authoritative.
The failed SQL is not an approved baseline: materially repair it and do not repeat a rejected
identifier, categorical value or dialect form.
Never add DDL, DML, comments, multiple statements, raw tables or unlisted sources. Do not invent a
date range merely because the source contains transactions. Use a temporal predicate only when the
question, approved baseline or an explicitly enforced catalog policy requires it. Keep an explicit
LIMIT no greater than max_allowed_rows. Return a complete SqlGenerationOutput. The SQL must not
contain Markdown fences.
""".strip()
        contract = dict(current_contract or {})
        compact_contract = {
            key: contract[key]
            for key in (
                "interpretation",
                "assumptions",
                "selected_metrics",
                "selected_dimensions",
                "selected_filters",
                "time_window",
                "source_objects",
                "query_spec_ref",
                "query_spec",
                "compiled_sql_artifact",
            )
            if key in contract
        }
        payload = {
            "question": question,
            "prior_compliance": {
                "retry_instruction": retry_instruction,
                "failed_sql": failed_sql,
            },
            "current_contract": compact_contract,
            "feedback_plan": feedback_plan or {},
            "resolved_query_spec": (feedback_plan or {}).get("resolved_query_spec"),
            "derived_changes": (feedback_plan or {}).get("derived_changes", []),
            "repair_context": repair_context,
            "max_allowed_rows": self.max_result_rows,
        }
        return await self.repair_llm.parse(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, default=str),
            response_model=SqlGenerationOutput,
        )

    async def generate(
        self,
        *,
        question: str,
        semantic_context: dict,
        history: list[dict[str, str]],
        structured_memory: dict | None = None,
        feedback: str | None = None,
        previous_sql: str | None = None,
        feedback_plan: dict | None = None,
        prior_compliance: dict | None = None,
        current_contract: dict | None = None,
    ) -> SqlGenerationOutput:
        compliance = dict(prior_compliance or {})
        if compliance.get("retry_instruction") and compliance.get("failed_sql"):
            return await self._repair(
                question=question,
                semantic_context=semantic_context,
                prior_compliance=compliance,
                current_contract=current_contract,
                feedback_plan=feedback_plan,
            )
        if feedback_plan and previous_sql:
            baseline_contract = dict(current_contract or {})
            if not baseline_contract:
                baseline_contract = self._contract_from_memory(structured_memory)
            return await self._revise(
                question=question,
                previous_sql=previous_sql,
                feedback=feedback,
                feedback_plan=dict(feedback_plan),
                semantic_context=semantic_context,
                current_contract=baseline_contract,
            )

        system = f"""
You are a senior analytics engineer generating governed {self.dialect} SQL.
Use only objects explicitly listed in allowed_sources. Use certified metrics and joins from the
semantic context. Never query raw, operational, analytics, system, or information_schema objects.
Generate one read-only SELECT statement. Never generate DDL, DML, CALL, COPY, comments,
multiple statements, temporary objects, or dynamic SQL. Do not invent a temporal predicate when the
user did not request one. Bound result size with the exact requested LIMIT and rely on deterministic
EXPLAIN/cost governance for scan safety. Use canonical syntax for the effective dialect. For PostgreSQL, prefer CURRENT_DATE for DATE
filters, TIMEZONE(zone, CURRENT_TIMESTAMP) instead of the infix AT TIME ZONE form, and canonical
intervals such as INTERVAL '1' MONTH. Do not mix syntax from different engines.
Prefer semantic aggregate views and trusted_queries when they answer the question. Do not fabricate columns.
Treat source_contracts as the authoritative per-view schema: after selecting a source, use
only columns published for that exact source, even when another semantic view exposes a similarly
named field. Use only allowed_values explicitly listed for categorical fields. Aggregated views
represent their published grain: aggregate certified measures with SUM; never use COUNT(*) to count
business events from an already aggregated view. Resolve relative dates from calendar_context.
For "ayer" use America/Lima calendar boundaries. Do not translate generic business words such as
executed, processed, completed, performed, ejecutada, procesada or realizada into a status value
unless that exact value is listed. For latest/top-N records, the request is complete without a date range: order by the catalog's
published timestamp or date descending and apply the exact requested LIMIT. Do not ask for dates.
Generic words such as executed, processed, completed, performed, ejecutada, procesada or realizada
refer to existing records unless the catalog publishes that exact categorical value; do not turn them
into a status filter and do not request clarification solely because no such status exists.
Return SQL without Markdown fences plus the business interpretation, assumptions, selected filters
and structured time window. ``selected_filters`` must contain only business predicates such as
status, channel, merchant or amount constraints. Do not duplicate lower or upper date boundaries in
``selected_filters`` when they are already represented by ``time_window``; temporal constraints have
one canonical owner. Preserve prior contract elements not explicitly changed by feedback.
When previous_sql is supplied, treat it as the approved baseline and modify only requested elements.
An exact requested LIMIT must be used unless it exceeds max_allowed_rows.
""".strip()
        user_payload = {
            "question": question,
            "semantic_context": semantic_context,
            "recent_conversation": history[-2:],
            "structured_memory": self._without_duplicate_sql(structured_memory),
            "previous_sql": previous_sql,
            "human_feedback": feedback,
            "feedback_plan": feedback_plan or {},
            "max_allowed_rows": self.max_result_rows,
        }
        return await self.llm.parse(
            system=system,
            user=json.dumps(user_payload, ensure_ascii=False, default=str),
            response_model=SqlGenerationOutput,
        )
