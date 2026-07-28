from __future__ import annotations

import json

from axiz.pe.sql_agent.models.contracts import CriticReviewOutput, InvestigationPlan
from axiz.pe.sql_agent.services.llm import StructuredLLM


class EvidenceCritiqueSkill:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    async def review(
        self,
        *,
        question: str,
        plan: InvestigationPlan,
        evidence: list[dict],
        budget_remaining: dict,
        available_specialists: list[dict],
    ) -> CriticReviewOutput:
        system = """
You are the independent critic in a governed analytical society. Evaluate only the supplied
query evidence and verification notes. Identify unsupported conclusions, contradictions and
missing evidence. Recommend additional tasks only when they are necessary and can be delegated to
an enabled specialist. You do not generate SQL, execute queries, change permissions, approve HITL
or alter budgets. Mark ready_to_finalize only when the evidence is sufficient for the user's scope.
Do not infer causality from correlation and do not treat unavailable data as proof.
""".strip()
        return await self.llm.parse(
            system=system,
            user=json.dumps(
                {
                    "question": question,
                    "plan": plan.model_dump(mode="json"),
                    "evidence": evidence,
                    "budget_remaining": budget_remaining,
                    "available_specialists": available_specialists,
                },
                ensure_ascii=False,
                default=str,
            ),
            response_model=CriticReviewOutput,
        )
