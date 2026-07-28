from __future__ import annotations

import json

from axiz.pe.sql_agent.models.contracts import QueryResult, VerificationOutput
from axiz.pe.sql_agent.services.llm import StructuredLLM


class EvidenceVerificationSkill:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    async def verify(
        self,
        *,
        question: str,
        interpretation: str,
        sql: str,
        result: QueryResult,
        raw_user_message: str = "",
        semantic_query_spec: dict | None = None,
        compiled_sql_artifact: dict | None = None,
    ) -> VerificationOutput:
        deterministic_observations: list[str] = []
        deterministic_caveats: list[str] = []
        if result.row_count == 0:
            deterministic_caveats.append("The query returned no rows")
        if result.truncated:
            deterministic_caveats.append("The result was truncated by the configured row limit")
        if len(result.columns) != len(set(result.columns)):
            deterministic_caveats.append("The result contains duplicate column names")
        artifact = dict(compiled_sql_artifact or {})
        if artifact and artifact.get("execution_state") != "executed":
            deterministic_caveats.append("The compiled SQL artifact is not marked as executed")
        artifact_violations = list((artifact.get("validation") or {}).get("violations") or [])
        if artifact_violations:
            deterministic_caveats.append(
                "The compiled SQL artifact has validation violations: "
                + "; ".join(artifact_violations)
            )

        system = """
You are a result verification agent. Check whether the SQL result can answer the user's question
and whether the interpretation is faithful. The latest semantic_query_spec is the analytical
source of truth; the SQL artifact is the executable source of truth. Contrast both with the raw user
message. Never invent values not present in the sample rows. Mark invalid for material problems such
as a spec/SQL mismatch, an artifact that was not executed, empty data when data was expected,
incompatible columns, obvious aggregation mistakes, or an unsupported answer.
""".strip()
        user = json.dumps(
            {
                "question": question,
                "interpretation": interpretation,
                "raw_user_message": raw_user_message,
                "semantic_query_spec": semantic_query_spec or {},
                "compiled_sql_artifact": compiled_sql_artifact or {},
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
        if result.row_count == 0 or artifact_violations or (
            artifact and artifact.get("execution_state") != "executed"
        ):
            verified.valid = False
            verified.confidence = min(verified.confidence, 0.4)
        return verified
