from __future__ import annotations

import json

from axiz.pe.sql_agent.models.contracts import (
    AutonomousBudget,
    AutonomousRoutingDecision,
    ConversationMemory,
)
from axiz.pe.sql_agent.services.agent_cache import AgentResponseCache
from axiz.pe.sql_agent.services.llm import StructuredLLM


class ComplexityRoutingSkill:
    """Select the smallest sufficient autonomous execution mode.

    The router is semantic and domain-neutral. It does not approve execution, change budgets,
    or bypass any deterministic control. A cache hit only avoids this classification call.
    """

    def __init__(self, llm: StructuredLLM, cache: AgentResponseCache | None = None) -> None:
        self.llm = llm
        self.cache = cache

    def _model_profile_projection(self) -> dict[str, object]:
        registry = getattr(self.llm, "registry", None)
        agent_name = getattr(self.llm, "agent_name", self.llm.__class__.__name__)
        if registry is None or not hasattr(registry, "profile_for"):
            return {"agent": agent_name, "adapter": self.llm.__class__.__name__}
        return registry.profile_for(agent_name).model_dump(mode="json")

    async def route(
        self,
        *,
        question: str,
        relation: str,
        domain: str | None,
        memory: ConversationMemory,
        specialists: list[dict],
        published_domains: list[dict],
        budget: AutonomousBudget,
        catalog_fingerprint: str,
    ) -> AutonomousRoutingDecision:
        system = """
You are the adaptive router for a governed autonomous analytics society.
Choose the smallest sufficient execution mode based on semantic complexity, not on fixed keywords,
domain names, metrics, dates, or examples.

Use direct_specialist when one independently verifiable analytical task, one specialist, and one
SQL evidence package can answer the request. Use full_investigation only when the request genuinely
requires multiple independent evidence packages, multiple specialists/domains, hypothesis testing,
causal or diagnostic investigation, reconciliation of contradictions, or iterative replanning.
A comparison can still be direct when one governed SQL query can produce the comparison.

For direct_specialist, select exactly one enabled non-critical specialist and return one standalone
task objective. For full_investigation, do not preselect a specialist; the planner will decompose the
request. Set query_mode=revise_previous only when the current message modifies the previously
approved SQL and the supplied memory contains it. Otherwise use new_evidence.

You cannot grant permissions, change limits, skip SQL security/cost checks, omit HITL, execute SQL,
or expand any budget. Ask for clarification when the request cannot be routed safely. Return concise
signals, not hidden reasoning.
""".strip()
        memory_projection = {
            "last_resolved_question": memory.last_resolved_question,
            "last_interpretation": memory.last_interpretation,
            "last_domain": memory.last_domain,
            "last_sql": memory.last_sql,
            "last_sql_snapshot": (
                memory.last_sql_snapshot.model_dump(mode="json")
                if memory.last_sql_snapshot else None
            ),
            "last_source_objects": list(memory.last_source_objects),
            "has_previous_sql": bool(memory.last_sql),
        }
        payload = {
            "contract_version": "coordinator-routing-v1",
            "question": question,
            "context_relation": relation,
            "detected_domain": domain,
            "structured_memory": memory_projection,
            "specialists": [
                {
                    "role": item.get("role"),
                    "display_name": item.get("display_name"),
                    "description": item.get("description"),
                    "domains": item.get("domains"),
                    "capabilities": item.get("capabilities"),
                    "enabled": item.get("enabled"),
                    "critical_reviewer": item.get("critical_reviewer"),
                }
                for item in specialists
            ],
            "published_domains": published_domains,
            "budget": budget.model_dump(mode="json"),
            "catalog_fingerprint": catalog_fingerprint,
            "model_profile": self._model_profile_projection(),
        }
        if self.cache is not None:
            cached = await self.cache.get("autonomous-routing", payload)
            if cached.hit and cached.value:
                try:
                    return AutonomousRoutingDecision.model_validate(cached.value)
                except Exception:
                    pass
        decision = await self.llm.parse(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, default=str),
            response_model=AutonomousRoutingDecision,
        )
        if self.cache is not None:
            await self.cache.set(
                "autonomous-routing",
                payload,
                decision.model_dump(mode="json"),
                ttl_seconds=900,
            )
        return decision
