from __future__ import annotations

import json

from axiz.pe.sql_agent.models.contracts import (
    AutonomousBudget,
    ConversationMemory,
    InvestigationPlan,
)
from axiz.pe.sql_agent.services.agent_cache import AgentResponseCache
from axiz.pe.sql_agent.services.llm import StructuredLLM


class InvestigationPlannerAgent:
    def __init__(self, llm: StructuredLLM, cache: AgentResponseCache | None = None) -> None:
        self.llm = llm
        self.cache = cache


    def _model_profile_projection(self) -> dict[str, object]:
        registry = getattr(self.llm, "registry", None)
        agent_name = getattr(self.llm, "agent_name", self.llm.__class__.__name__)
        if registry is None or not hasattr(registry, "profile_for"):
            return {"agent": agent_name, "adapter": self.llm.__class__.__name__}
        return registry.profile_for(agent_name).model_dump(mode="json")

    async def plan(
        self,
        *,
        question: str,
        memory: ConversationMemory,
        specialists: list[dict],
        published_domains: list[dict],
        context_relation: str,
        budget: AutonomousBudget,
    ) -> InvestigationPlan:
        system = """
You are the planner in a governed society of enterprise analytics agents.
Create the smallest evidence plan that can answer the user's request. Delegate only to specialists
whose enabled field is true. Each task must ask one concrete analytical question answerable through
the published semantic catalog. Set query_mode=revise_previous only when the task directly changes
the previously approved SQL; use query_mode=new_evidence for independent supporting queries. Use dependencies only when a later task genuinely needs earlier
evidence. Do not create a critic task: the critic runs automatically after evidence collection.
Never grant permissions, bypass SQL security, omit human approval, or exceed the supplied budgets.
For a simple request create one task. For cross-domain or diagnostic requests create multiple tasks
only when the evidence is necessary. Do not invent unavailable domains, metrics or data.
""".strip()
        payload = {
            "contract_version": "investigation-plan-v2",
            "question": question,
            "structured_memory": memory.model_dump(mode="json"),
            "specialists": specialists,
            "published_domains": published_domains,
            "context_relation": context_relation,
            "budget": budget.model_dump(mode="json"),
            "model_profile": self._model_profile_projection(),
        }
        if self.cache is not None:
            cached = await self.cache.get("investigation-plan", payload)
            if cached.hit and cached.value:
                try:
                    return InvestigationPlan.model_validate(cached.value)
                except Exception:
                    pass
        plan = await self.llm.parse(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, default=str),
            response_model=InvestigationPlan,
        )
        if self.cache is not None:
            await self.cache.set(
                "investigation-plan", payload, plan.model_dump(mode="json"), ttl_seconds=600
            )
        return plan
