from __future__ import annotations

import json

from axiz.pe.sql_agent.models.contracts import SqlGenerationOutput
from axiz.pe.sql_agent.services.llm import StructuredLLM


class SqlGeneratorAgent:
    def __init__(self, llm: StructuredLLM, dialect: str, max_result_rows: int) -> None:
        self.llm = llm
        self.dialect = dialect
        self.max_result_rows = max_result_rows

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
    ) -> SqlGenerationOutput:
        system = f"""
You are a senior analytics engineer generating governed {self.dialect} SQL.
Use only objects explicitly listed in allowed_sources. Use certified metrics and joins from the
semantic context. Never query raw, operational, analytics, system, or information_schema objects.
Generate one read-only SELECT statement. Never generate DDL, DML, CALL, COPY, comments,
multiple statements, temporary objects, or dynamic SQL. Always bound transaction data by date.
Use canonical syntax for the effective dialect. For PostgreSQL, prefer CURRENT_DATE for DATE
filters, TIMEZONE(zone, CURRENT_TIMESTAMP) instead of the infix AT TIME ZONE form, and canonical
intervals such as INTERVAL '1' MONTH. Do not mix syntax from different engines.
Prefer semantic aggregate views when they answer the question. Do not fabricate columns.
Treat the semantic dimensions and their column fields as the exact available identifiers. Use only
allowed_values explicitly listed for categorical fields. Do not translate generic business words
such as executed, processed, completed, performed, ejecutada, procesada or realizada into a status
value unless that exact value is present in allowed_values or the user explicitly requested a listed
status. For requests for the latest or most recent records, order by the catalog timestamp dimension
when one exists; otherwise use the catalog date dimension.
Return the SQL without Markdown fences and explain the business interpretation and assumptions.
Also return selected_filters as field/operator/value/source records and a structured time_window.
Use source="inherited" only for filters inherited from structured memory; otherwise use source="user".
Human feedback is mandatory. Apply every required change in feedback_plan, including combined changes in one request. Preserve metrics, dimensions, filters, time window, ordering and sources that were not explicitly changed. When feedback requests an exact numeric LIMIT, use exactly that LIMIT unless it exceeds max_allowed_rows; never keep the previous LIMIT merely because it appears in previous_sql or examples. If prior_compliance lists missing changes, correct each one before returning.
When previous_sql is supplied for a conversational follow-up, treat it as the approved baseline.
Keep its LIMIT, ORDER BY, projection, grouping, non-target filters and semantic sources unchanged
unless feedback_plan explicitly requests a change to that category. Never replace a prior LIMIT with
max_allowed_rows as a default. When prior_compliance contains retry_instruction and failed_sql, the
failed SQL is not an approved baseline: materially repair it, do not repeat the rejected identifier
or value, and follow the database feedback using exact semantic-context names.
""".strip()
        user_payload = {
            "question": question,
            "semantic_context": semantic_context,
            "recent_conversation": history[-6:],
            "structured_memory": structured_memory or {},
            "previous_sql": previous_sql,
            "human_feedback": feedback,
            "feedback_plan": feedback_plan or {},
            "prior_compliance": prior_compliance or {},
            "max_allowed_rows": self.max_result_rows,
        }
        return await self.llm.parse(
            system=system,
            user=json.dumps(user_payload, ensure_ascii=False, default=str),
            response_model=SqlGenerationOutput,
        )
