from __future__ import annotations

import json

from axiz.pe.sql_agent.models.contracts import (
    AutonomousSynthesisOutput,
    CriticReviewOutput,
    InvestigationPlan,
    SupervisorDecision,
)
from axiz.pe.sql_agent.services.llm import StructuredLLM


class SupervisionSkill:
    def __init__(self, llm: StructuredLLM, synthesis_llm: StructuredLLM) -> None:
        self.llm = llm
        self.synthesis_llm = synthesis_llm

    async def decide(
        self,
        *,
        question: str,
        plan: InvestigationPlan,
        evidence: list[dict],
        critic: CriticReviewOutput | None,
        budget_usage: dict,
        available_specialists: list[dict],
    ) -> SupervisorDecision:
        system = """
You are the autonomous supervisor of a governed analytical society. Decide how to continue the
investigation: delegate a pending task, request necessary evidence, reject an unsupported
conclusion, ask for clarification, or finalize. You may create new tasks only within the supplied
budget and only for enabled specialists. You cannot generate or execute SQL, alter permissions,
bypass deterministic security/cost controls, omit human approval, or exceed query/token/time/task
budgets. Prefer the smallest sufficient investigation. Finalize only when there is verified
evidence. When delegating, use next_task_ids for independent tasks that can be prepared in
parallel, up to the supplied parallel limit; keep next_task_id only for compatibility. Never select
tasks with unresolved dependencies.
""".strip()
        return await self.llm.parse(
            system=system,
            user=json.dumps(
                {
                    "question": question,
                    "plan": plan.model_dump(mode="json"),
                    "evidence": evidence,
                    "critic_review": critic.model_dump(mode="json") if critic else None,
                    "budget_usage": budget_usage,
                    "available_specialists": available_specialists,
                },
                ensure_ascii=False,
                default=str,
            ),
            response_model=SupervisorDecision,
        )

    async def synthesize(
        self,
        *,
        question: str,
        plan: InvestigationPlan,
        evidence: list[dict],
        critic: CriticReviewOutput | None,
        rejected_conclusions: list[str],
    ) -> AutonomousSynthesisOutput:
        system = """
You are the final synthesis function of a governed analytical supervisor. Answer in the user's
language using only the supplied verified evidence. State the conclusion first, then concise
findings and caveats. Reconcile contradictions explicitly. Never claim evidence that is absent,
never expose hidden reasoning and never present rejected conclusions as facts. Return findings as
evidence-backed objects. Every finding must cite one or more existing evidence_ids and state
limitations. Select the most useful evidence_id as primary_evidence_id for the UI table or chart.
""".strip()
        return await self.synthesis_llm.parse(
            system=system,
            user=json.dumps(
                {
                    "question": question,
                    "plan": plan.model_dump(mode="json"),
                    "evidence": evidence,
                    "critic_review": critic.model_dump(mode="json") if critic else None,
                    "rejected_conclusions": rejected_conclusions,
                },
                ensure_ascii=False,
                default=str,
            ),
            response_model=AutonomousSynthesisOutput,
        )
