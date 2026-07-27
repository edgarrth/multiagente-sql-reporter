from __future__ import annotations

import json
import re
from typing import Any

from axiz.pe.sql_agent.models.contracts import SqlGenerationOutput
from axiz.pe.sql_agent.services.llm import StructuredLLM


_SOURCE_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w$]*\.[A-Za-z_][\w$]*)",
    re.IGNORECASE,
)


class SqlGeneratorAgent:
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
        sources: list[str] = []
        for match in _SOURCE_PATTERN.finditer(sql or ""):
            source = match.group(1)
            if source not in sources:
                sources.append(source)
        return sources

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
You revise one previously approved governed {self.dialect} SELECT using a validated typed feedback
plan. Apply every required change exactly once and preserve projection, metrics, dimensions, filters,
grouping, ordering, time window, sources and LIMIT that are not targeted. Use only exact columns and
sources in revision_context.source_contracts. Never add DDL, DML, comments, multiple statements, raw
tables or unlisted sources. Keep date boundaries and an explicit LIMIT no greater than
max_allowed_rows. Return a complete SqlGenerationOutput without Markdown fences.
""".strip()
        payload = {
            "question": question,
            "human_feedback": feedback or "",
            "previous_sql": previous_sql,
            "feedback_plan": feedback_plan,
            "current_contract": current_contract,
            "revision_context": self._revision_context(semantic_context),
            "max_allowed_rows": self.max_result_rows,
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
Never add DDL, DML, comments, multiple statements, raw tables or unlisted sources. Keep transaction
queries date-bounded and keep an explicit LIMIT no greater than max_allowed_rows. Return a complete
SqlGenerationOutput. The SQL must not contain Markdown fences.
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
multiple statements, temporary objects, or dynamic SQL. Always bound transaction data by date.
Use canonical syntax for the effective dialect. For PostgreSQL, prefer CURRENT_DATE for DATE
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
unless that exact value is listed. For latest records, order by the published timestamp or date.
Return SQL without Markdown fences plus the business interpretation, assumptions, selected filters
and structured time window. Preserve prior contract elements not explicitly changed by feedback.
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
