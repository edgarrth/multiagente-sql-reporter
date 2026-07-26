from __future__ import annotations

import json
import math
from typing import Any

from axiz.pe.sql_agent.models.contracts import (
    CostValidation,
    LLMApprovalEstimate,
    LLMPlannedCallEstimate,
    SecurityValidation,
)
from axiz.pe.sql_agent.services.llm import AgentModelRegistry, PromptBudget


class LLMApprovalTokenEstimator:
    """Estimates the LLM work that remains after a user approves a SQL proposal.

    Security validation, EXPLAIN and SQL execution are deterministic tools and consume no LLM
    tokens. In the analytical path, approval normally triggers two model calls: result
    verification and result explanation. The result does not exist yet, so the estimate uses the
    PostgreSQL plan row count/width and the same sample limits used by those agents.
    """

    _VERIFIER_SYSTEM = """
You are a result verification agent. Check whether the SQL result can answer the user's question
and whether the interpretation is faithful. Never invent values not present in the sample rows.
Mark invalid only for material problems such as empty data when data was expected, incompatible
columns, obvious aggregation mistakes, or an answer that cannot be supported.
""".strip()

    _EXPLANATION_SYSTEM = """
You are an enterprise analytics explanation agent. Answer in the same language as the user.
Use only the supplied rows and verification notes. State the main conclusion first, then concise
findings and caveats. Do not claim causality. Do not expose hidden reasoning or sensitive data.
Return a table-oriented visualization placeholder; chart selection is applied deterministically.
""".strip()

    def __init__(self, registry: AgentModelRegistry, max_result_rows: int) -> None:
        self.registry = registry
        self.max_result_rows = max_result_rows

    def estimate(
        self,
        *,
        question: str,
        interpretation: str,
        sql: str,
        security: SecurityValidation,
        cost: CostValidation,
    ) -> LLMApprovalEstimate:
        projected_rows = max(
            0,
            min(
                int(cost.plan_rows or self.max_result_rows),
                int(security.enforced_limit or security.max_rows or self.max_result_rows),
                self.max_result_rows,
            ),
        )
        projected_width = max(32, int(cost.plan_width or 128))
        column_count = max(1, len(security.columns))

        calls = [
            self._call_estimate(
                agent="result_verifier",
                system=self._VERIFIER_SYSTEM,
                envelope={
                    "question": question,
                    "interpretation": interpretation,
                    "sql": sql,
                    "columns": security.columns,
                    "row_count": projected_rows,
                    "sample_rows": "<projected result rows>",
                    "deterministic_observations": [],
                    "deterministic_caveats": [],
                },
                sample_rows=min(projected_rows, 20),
                row_width=projected_width,
                column_count=column_count,
                expected_output_cap=500,
                basis="Verificación del resultado con hasta 20 filas de muestra.",
            ),
            self._call_estimate(
                agent="explanation",
                system=self._EXPLANATION_SYSTEM,
                envelope={
                    "question": question,
                    "interpretation": interpretation,
                    "columns": security.columns,
                    "rows": "<projected result rows>",
                    "row_count": projected_rows,
                    "verification": "<verification output>",
                },
                sample_rows=min(projected_rows, 100),
                row_width=projected_width,
                column_count=column_count,
                expected_output_cap=1_000,
                basis="Explicación del resultado con hasta 100 filas de muestra.",
            ),
        ]
        return LLMApprovalEstimate(
            expected_call_count=len(calls),
            estimated_input_tokens=sum(item.estimated_input_tokens for item in calls),
            estimated_output_tokens=sum(item.estimated_output_tokens for item in calls),
            estimated_total_tokens=sum(item.estimated_total_tokens for item in calls),
            maximum_total_tokens=sum(item.maximum_total_tokens for item in calls),
            projected_result_rows=projected_rows,
            projected_row_width_bytes=projected_width,
            assumptions=[
                (
                    "La aprobación ejecutará herramientas determinísticas y luego dos "
                    "llamadas LLM: verificación y explicación."
                ),
                (
                    "Las filas todavía no existen; el tamaño de entrada se aproxima con "
                    "Plan Rows, Plan Width y los límites de muestra de cada agente."
                ),
                (
                    "El máximo configurado reserva max_output_tokens y no equivale al "
                    "consumo esperado."
                ),
            ],
            calls=calls,
        )

    def _call_estimate(
        self,
        *,
        agent: str,
        system: str,
        envelope: dict[str, Any],
        sample_rows: int,
        row_width: int,
        column_count: int,
        expected_output_cap: int,
        basis: str,
    ) -> LLMPlannedCallEstimate:
        profile = self.registry.profile_for(agent)
        base_input = PromptBudget.estimate_tokens(system) + PromptBudget.estimate_tokens(
            json.dumps(envelope, ensure_ascii=False, default=str)
        )
        # PostgreSQL Plan Width is binary row width, while JSON includes keys, quoting and
        # separators. The 1.6 expansion plus per-column overhead is deliberately conservative.
        projected_row_characters = sample_rows * (
            math.ceil(row_width * 1.6) + column_count * 12 + 8
        )
        row_tokens = math.ceil(projected_row_characters / 3.5)
        estimated_input = min(profile.max_input_tokens, base_input + row_tokens)
        estimated_output = min(profile.max_output_tokens, expected_output_cap)
        return LLMPlannedCallEstimate(
            agent=agent,
            provider=profile.provider,
            model=profile.model,
            estimated_input_tokens=estimated_input,
            estimated_output_tokens=estimated_output,
            estimated_total_tokens=estimated_input + estimated_output,
            max_output_tokens=profile.max_output_tokens,
            maximum_total_tokens=estimated_input + profile.max_output_tokens,
            basis=basis,
        )
