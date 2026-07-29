from __future__ import annotations

import json
from typing import Any

from axiz.pe.sql_agent.models.contracts import SqlRevisionReviewOutput
from axiz.pe.sql_agent.services.llm import StructuredLLM
from axiz.pe.sql_agent.tools.sql_revision_diff import SqlRevisionDiffAnalyzer


class SqlRevisionReviewSkill:
    """Review an open-ended SQL revision against the user's complete feedback.

    The reviewer intentionally uses no fixed vocabulary of feedback targets. It compares the
    previous and revised SQL as complete programs and checks the raw request against the AST diff.
    Deterministic security, catalog, cost, and execution gates remain separate.
    """

    def __init__(self, llm: StructuredLLM, dialect: str = "postgres") -> None:
        self.llm = llm
        self.diff_analyzer = SqlRevisionDiffAnalyzer(dialect=dialect)

    async def validate(
        self,
        *,
        raw_user_message: str,
        previous_sql: str,
        final_sql: str,
        semantic_context: dict[str, Any],
        interpretation: str = "",
        change_summary: list[str] | None = None,
    ) -> SqlRevisionReviewOutput:
        system = """
You are an independent reviewer of an open-ended SQL revision. Evaluate the user's complete raw
message against the complete previous SQL and complete revised SQL. Do not force the request into
a fixed list of filters, dates, metrics, projections, joins, or clauses.

Approve when the final SQL implements every material requirement and preserves unrelated behavior.
Use the AST diff only as structural evidence; understand the user's natural language directly.
A valid request may change any SQL construct that is supported by the published catalog. Do not
ask for clarification merely because a predefined property is absent. Ask only when two materially
different business meanings remain plausible after considering the prior SQL and catalog.

Do not judge write safety, source allowlists, database cost, or syntax viability here; deterministic
gates handle those concerns. Return concrete missing requirements and a concise repair instruction
when the revision is incomplete. Never invent a date filter, metric, dimension, predicate, grouping,
ordering, join, or limit that is absent from both the request and the relevant catalog meaning.
""".strip()
        payload = {
            "raw_user_message": raw_user_message,
            "previous_sql": previous_sql,
            "final_sql": final_sql,
            "sql_ast_diff": self.diff_analyzer.compare(previous_sql, final_sql),
            "interpretation": interpretation,
            "change_summary": change_summary or [],
            "published_catalog": {
                "source_contracts": semantic_context.get("source_contracts", {}),
                "semantic_symbols": semantic_context.get("semantic_symbols", {}),
            },
        }
        result = await self.llm.parse(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, default=str),
            response_model=SqlRevisionReviewOutput,
        )
        if not result.compliant and not result.failed_sql:
            result.failed_sql = final_sql
        return result

