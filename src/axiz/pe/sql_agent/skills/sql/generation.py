from __future__ import annotations

import json
from typing import Any

from axiz.pe.sql_agent.models.contracts import SqlGenerationOutput
from axiz.pe.sql_agent.services.llm import StructuredLLM
from axiz.pe.sql_agent.tools.sql_ast_analyzer import SqlAstAnalyzer


class SqlGenerationSkill:
    """Autonomous full-SQL generation, revision, and repair.

    Natural-language requests are not compiled into a closed set of feedback properties. The model
    receives the complete request, the relevant semantic catalog, and—when revising—the complete
    approved SQL. Generic deterministic gates validate the produced statement afterwards.
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
        self.analyzer = SqlAstAnalyzer(dialect=dialect)

    @staticmethod
    def _bounded_history(history: list[dict[str, str]], limit: int = 4) -> list[dict[str, str]]:
        return [
            {
                "role": str(item.get("role") or "unknown")[:32],
                "content": str(item.get("content") or "")[:1600],
            }
            for item in history[-limit:]
        ]

    @staticmethod
    def _memory_projection(memory: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(memory or {})
        return {
            "last_user_request": payload.get("last_user_request"),
            "last_resolved_question": payload.get("last_resolved_question"),
            "last_interpretation": payload.get("last_interpretation"),
            "last_domain": payload.get("last_domain"),
            "last_sql_snapshot": payload.get("last_sql_snapshot"),
            "last_result_schema": list(payload.get("last_result_schema") or [])[:80],
            "last_row_count": payload.get("last_row_count"),
        }

    @staticmethod
    def _semantic_projection(context: dict[str, Any]) -> dict[str, Any]:
        """Preserve published catalog metadata without a fixed business-property whitelist.

        Retrieval and context-size controls happen before this stage. Dropping unknown catalog keys
        here would make every future semantic property require a code change, so complete selected
        source contracts and symbols are passed through unchanged.
        """
        return {
            "allowed_sources": list(context.get("allowed_sources") or []),
            "source_contracts": {
                str(source): dict(contract or {})
                for source, contract in dict(
                    context.get("source_contracts") or {}
                ).items()
            },
            "semantic_symbols": dict(context.get("semantic_symbols") or {}),
            "calendar_context": dict(context.get("calendar_context") or {}),
            "query_policy": dict(context.get("query_policy") or {}),
            "domain_definition": dict(context.get("domain_definition") or {}),
        }

    def _sources_from_sql(self, sql: str) -> list[str]:
        try:
            return self.analyzer.sources(self.analyzer.parse(sql))
        except Exception:
            return []

    def _repair_context(
        self,
        semantic_context: dict[str, Any],
        failed_sql: str,
    ) -> dict[str, Any]:
        projected = self._semantic_projection(semantic_context)
        contracts = dict(projected.get("source_contracts") or {})
        used = self._sources_from_sql(failed_sql)
        if used:
            projected["source_contracts"] = {
                source: contracts[source]
                for source in used
                if source in contracts
            }
            projected["allowed_sources"] = [
                source for source in projected.get("allowed_sources", []) if source in used
            ] or used
        return projected

    async def _generate(
        self,
        *,
        question: str,
        semantic_context: dict[str, Any],
        history: list[dict[str, str]],
        structured_memory: dict[str, Any] | None,
    ) -> SqlGenerationOutput:
        system = f"""
You are the SQL Engineer in a governed autonomous analytical society. Construct one complete,
read-only {self.dialect} SELECT statement that answers the user's request using the published
semantic catalog.

Autonomy rules:
- Infer the query shape from the user's complete meaning and the catalog. Do not require a fixed
  checklist of dates, filters, metrics, dimensions, ordering, or grouping.
- Do not add a predicate, date range, grouping, aggregation, or status value unless the request or
  published semantic definition requires it.
- A request is complete when the catalog provides enough information to produce one reasonable
  query. Ask a clarification only when two materially different business answers remain equally
  valid and the catalog/history cannot resolve them.
- Use only published sources, columns, relationships, and categorical values. Reuse certified
  formulas when the request names a governed metric, but you may build transparent derived
  calculations from published members when required by the user's objective.
- Select the most useful published columns for detail requests; for analytical requests choose the
  required expressions, grouping, and ordering autonomously.
- Honor explicit row counts and ordering. Otherwise include a safe LIMIT no greater than
  max_allowed_rows for non-scalar results.

Governance rules:
- Produce exactly one SELECT or WITH...SELECT statement; no DDL, DML, CALL, COPY, comments,
  temporary objects, dynamic SQL, or multiple statements.
- Never use raw/system/unpublished objects. Never fabricate identifiers.
- Return the complete SqlGenerationOutput without Markdown fences. The SQL is the authoritative
  generated artifact; interpretation and assumptions explain it without duplicating a fixed
  semantic-property schema.
""".strip()
        payload = {
            "question": question,
            "semantic_catalog": self._semantic_projection(semantic_context),
            "recent_conversation": self._bounded_history(history),
            "session_memory": self._memory_projection(structured_memory),
            "max_allowed_rows": self.max_result_rows,
        }
        return await self.llm.parse(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, default=str),
            response_model=SqlGenerationOutput,
        )

    async def _revise(
        self,
        *,
        question: str,
        previous_sql: str,
        feedback: str,
        semantic_context: dict[str, Any],
    ) -> SqlGenerationOutput:
        system = f"""
You are the SQL Engineer in a governed autonomous analytical society. Revise the complete prior
{self.dialect} SQL according to the user's complete natural-language feedback.

Revision rules:
- The full previous SQL and full feedback are the revision contract. Do not translate the request
  into a closed list of feedback types.
- Return the complete revised SQL, not a patch.
- Apply every explicit change and preserve all unrequested behavior.
- You may change any valid SQL construct: projections and their order, expressions, aliases,
  predicates, joins, CTEs, grouping, aggregates, windows, HAVING, ordering, limits, and published
  semantic sources.
- Reconcile dependencies across clauses. Removed or renamed expressions must not remain referenced
  by ORDER BY, GROUP BY, HAVING, windows, or derived expressions.
- Use only exact sources, columns, relationships, and categorical values published in the
  supplied catalog. Certified formulas remain authoritative for named metrics, while transparent
  derived expressions may be constructed from published members. Never invent a requirement merely
  because it appeared in an older metadata field.
- Ask one clarification only when the feedback still has multiple materially different business
  meanings after considering the baseline SQL, original question, and catalog. Otherwise revise it.
- Keep the statement read-only, single-statement, and within max_allowed_rows.

Return a complete SqlGenerationOutput without Markdown fences. change_summary must describe the
actual edits made.
""".strip()
        payload = {
            "original_question": question,
            "raw_user_feedback": feedback,
            "previous_sql": previous_sql,
            "semantic_catalog": self._semantic_projection(semantic_context),
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
        failed_sql: str,
        validator_feedback: str,
        semantic_context: dict[str, Any],
        feedback: str | None,
        previous_sql: str | None,
    ) -> SqlGenerationOutput:
        system = f"""
You repair one complete {self.dialect} SELECT rejected by generic deterministic or semantic
validation. Correct every reported issue while preserving the user's requested result.

Use only published catalog identifiers and categorical values. Certified formulas remain
preferred for named governed metrics, while transparent derived expressions may use published
members. Do not add unrelated filters or assumptions. Reconcile all cross-clause dependencies.
Return one complete read-only statement with a LIMIT no greater than max_allowed_rows. Do not
return Markdown fences.
""".strip()
        payload = {
            "question": question,
            "raw_user_feedback": feedback,
            "approved_baseline_sql": previous_sql,
            "failed_sql": failed_sql,
            "validator_feedback": validator_feedback,
            "semantic_catalog": self._repair_context(semantic_context, failed_sql),
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
        semantic_context: dict[str, Any],
        history: list[dict[str, str]],
        structured_memory: dict[str, Any] | None = None,
        feedback: str | None = None,
        previous_sql: str | None = None,
        prior_review: dict[str, Any] | None = None,
        **_: Any,
    ) -> SqlGenerationOutput:
        review = dict(prior_review or {})
        failed_sql = str(review.get("failed_sql") or "").strip()
        retry_instruction = str(review.get("retry_instruction") or "").strip()
        if failed_sql and retry_instruction:
            return await self._repair(
                question=question,
                failed_sql=failed_sql,
                validator_feedback=retry_instruction,
                semantic_context=semantic_context,
                feedback=feedback,
                previous_sql=previous_sql,
            )
        if previous_sql and (feedback or "").strip():
            return await self._revise(
                question=question,
                previous_sql=previous_sql,
                feedback=str(feedback).strip(),
                semantic_context=semantic_context,
            )
        return await self._generate(
            question=question,
            semantic_context=semantic_context,
            history=history,
            structured_memory=structured_memory,
        )
