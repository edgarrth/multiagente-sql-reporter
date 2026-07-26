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
    ) -> SqlGenerationOutput:
        system = f"""
You are a senior analytics engineer generating governed {self.dialect} SQL.
Use only objects explicitly listed in allowed_sources. Use certified metrics and joins from the
semantic context. Never query raw, operational, analytics, system, or information_schema objects.
Generate one read-only SELECT statement. Never generate DDL, DML, CALL, COPY, comments,
multiple statements, temporary objects, or dynamic SQL. Always bound transaction data by date.
Prefer semantic aggregate views when they answer the question. Do not fabricate columns.
Return the SQL without Markdown fences and explain the business interpretation and assumptions.
Also return selected_filters as field/operator/value/source records and a structured time_window.
Use source="inherited" only for filters inherited from structured memory; otherwise use source="user".
Human feedback is mandatory. When it requests an exact numeric LIMIT, use exactly that LIMIT unless it exceeds max_allowed_rows; never keep the previous LIMIT merely because it appears in previous_sql or examples.
""".strip()
        user_payload = {
            "question": question,
            "semantic_context": semantic_context,
            "recent_conversation": history[-6:],
            "structured_memory": structured_memory or {},
            "previous_sql": previous_sql,
            "human_feedback": feedback,
            "max_allowed_rows": self.max_result_rows,
        }
        return await self.llm.parse(
            system=system,
            user=json.dumps(user_payload, ensure_ascii=False, default=str),
            response_model=SqlGenerationOutput,
        )
