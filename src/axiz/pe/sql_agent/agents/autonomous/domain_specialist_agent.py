from __future__ import annotations

import json
from typing import Any

from axiz.pe.sql_agent.models.contracts import (
    ConversationMemory,
    InvestigationTask,
    SpecialistProposalReview,
    SpecialistTaskOutput,
)
from axiz.pe.sql_agent.services.llm import StructuredLLM
from axiz.pe.sql_agent.services.specialist_registry import SpecialistProfile


class DomainSpecialistAgent:
    """Specialist reasoning for task refinement and risk-based proposal review.

    The surrounding LangGraph subgraph supplies tools and deterministic gates. This class never
    executes SQL or changes authority.
    """

    def __init__(self, profile: SpecialistProfile, llm: StructuredLLM) -> None:
        self.profile = profile
        self.llm = llm

    @staticmethod
    def _memory_projection(memory: ConversationMemory) -> dict[str, Any]:
        return {
            "last_resolved_question": memory.last_resolved_question,
            "last_interpretation": memory.last_interpretation,
            "domain": memory.last_domain,
            "metrics": list(memory.last_metrics),
            "dimensions": list(memory.last_dimensions),
            "filters": [item.model_dump(mode="json") for item in memory.last_filters],
            "time_window": (
                memory.last_time_window.model_dump(mode="json")
                if memory.last_time_window
                else None
            ),
            "ordering": list(memory.last_ordering),
            "limit": memory.last_limit,
            "sources": list(memory.last_source_objects),
            "has_previous_sql": bool(memory.last_sql),
        }

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
Refine the delegated objective into one standalone analytical question. Preserve query_mode
exactly. Select only a published semantic domain allowed by your profile. Return a short
catalog_focus containing the concepts that retrieval should prioritize. Describe the evidence
needed, not SQL. You cannot execute tools, change permissions, approve security, skip HITL or
expand budgets. If the catalog cannot support the task, return can_proceed=false with a precise
block_reason. Preserve the user's language and do not expose hidden reasoning.
""".strip()
        return await self.llm.parse(
            system=system,
            user=json.dumps(
                {
                    "task": task.model_dump(mode="json"),
                    "original_question": original_question,
                    "memory": self._memory_projection(memory),
                    "profile": {
                        "role": self.profile.role,
                        "display_name": self.profile.display_name,
                        "description": self.profile.description,
                        "domains": self.profile.domains,
                        "capabilities": self.profile.capabilities,
                    },
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
You are the risk-based self-review stage of {self.profile.display_name}. Evaluate whether the
proposed SQL and analytical contract answer the delegated task using only the compact published
semantic context. This call is made only when deterministic risk routing found a reason for an
additional semantic review. You cannot approve permissions, SQL security, query cost, HITL,
budgets or execution; those are immutable gates. Reject unsupported semantics, unrelated scope
changes or insufficient evidence. Return a concise retry_instruction when repair is possible. Do
not expose hidden reasoning.
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
