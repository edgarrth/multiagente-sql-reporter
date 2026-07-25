from __future__ import annotations

import json

from axiz.pe.sql_agent.models.contracts import QueryResult, VerificationOutput
from axiz.pe.sql_agent.services.llm import StructuredLLM


class ResultVerifierAgent:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    async def verify(
        self,
        *,
        question: str,
        interpretation: str,
        sql: str,
        result: QueryResult,
    ) -> VerificationOutput:
        deterministic_observations: list[str] = []
        deterministic_caveats: list[str] = []
        if result.row_count == 0:
            deterministic_caveats.append("The query returned no rows")
        if result.truncated:
            deterministic_caveats.append("The result was truncated by the configured row limit")
        if len(result.columns) != len(set(result.columns)):
            deterministic_caveats.append("The result contains duplicate column names")

        system = """
You are a result verification agent. Check whether the SQL result can answer the user's question
and whether the interpretation is faithful. Never invent values not present in the sample rows.
Mark invalid only for material problems such as empty data when data was expected, incompatible
columns, obvious aggregation mistakes, or an answer that cannot be supported.
""".strip()
        user = json.dumps(
            {
                "question": question,
                "interpretation": interpretation,
                "sql": sql,
                "columns": result.columns,
                "row_count": result.row_count,
                "sample_rows": result.rows[:20],
                "deterministic_observations": deterministic_observations,
                "deterministic_caveats": deterministic_caveats,
            },
            ensure_ascii=False,
            default=str,
        )
        verified = await self.llm.parse(
            system=system,
            user=user,
            response_model=VerificationOutput,
        )
        verified.observations = deterministic_observations + verified.observations
        verified.caveats = deterministic_caveats + verified.caveats
        if result.row_count == 0:
            verified.valid = False
            verified.confidence = min(verified.confidence, 0.4)
        return verified
