from __future__ import annotations

import json

from axiz.pe.sql_agent.models.contracts import (
    ConversationMemory,
    InvestigationTask,
    SpecialistProposalReview,
    SpecialistTaskOutput,
)
from axiz.pe.sql_agent.services.llm import StructuredLLM
from axiz.pe.sql_agent.services.specialist_registry import SpecialistProfile


class DomainSpecialistAgent:
    """Single-call specialist that refines a delegated task; it never executes SQL directly."""

    def __init__(self, profile: SpecialistProfile, llm: StructuredLLM) -> None:
        self.profile = profile
        self.llm = llm

    async def prepare(
        self,
        *,
        task: InvestigationTask,
        original_question: str,
        memory: ConversationMemory,
        published_domains: list[dict],
        prior_evidence: list[dict],
    ) -> SpecialistTaskOutput:
        system = f"""
You are {self.profile.display_name}, a specialist in a governed analytical society.
{self.profile.instructions}
Refine the delegated objective into one standalone analytical question. Preserve the delegated
query_mode exactly; you cannot decide whether to reuse a previous SQL. Select only a published
semantic domain allowed by your profile. Describe the evidence needed, not the SQL. You cannot
execute tools, change permissions, approve security, skip HITL or expand budgets. If the available
catalog cannot support the task, return can_proceed=false and a precise block_reason. Preserve the
user's language and do not expose hidden reasoning.
""".strip()
        return await self.llm.parse(
            system=system,
            user=json.dumps(
                {
                    "task": task.model_dump(mode="json"),
                    "original_question": original_question,
                    "memory": memory.model_dump(mode="json"),
                    "profile": self.profile.model_dump(mode="json"),
                    "published_domains": published_domains,
                    "prior_evidence": prior_evidence,
                },
                ensure_ascii=False,
                default=str,
            ),
            response_model=SpecialistTaskOutput,
        )


    async def review_proposal(
        self,
        *,
        task: InvestigationTask,
        prepared: SpecialistTaskOutput,
        generated_contract: dict,
        final_sql: str,
        semantic_context: dict,
        security_validation: dict,
        cost_validation: dict,
    ) -> SpecialistProposalReview:
        system = f"""
You are the self-review stage of {self.profile.display_name}. Evaluate whether the proposed SQL
and analytical contract answer the delegated task using only the published semantic context.
You cannot approve permissions, SQL security, query cost, HITL, budgets or execution; those are
deterministic gates. Treat security_validation and cost_validation as immutable facts. Reject the
proposal when it does not match the task, uses unsupported semantics, changes unrelated scope or
would provide insufficient evidence. Return a concise retry_instruction when repair is possible.
Do not expose hidden reasoning.
""".strip()
        return await self.llm.parse(
            system=system,
            user=json.dumps(
                {
                    "task": task.model_dump(mode="json"),
                    "prepared_task": prepared.model_dump(mode="json"),
                    "generated_contract": generated_contract,
                    "final_sql": final_sql,
                    "semantic_context": semantic_context,
                    "security_validation": security_validation,
                    "cost_validation": cost_validation,
                },
                ensure_ascii=False,
                default=str,
            ),
            response_model=SpecialistProposalReview,
        )
