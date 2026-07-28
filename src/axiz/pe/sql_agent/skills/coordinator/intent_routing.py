from __future__ import annotations

import json
from typing import Any

from axiz.pe.sql_agent.models.contracts import Intent, IntentDomainOutput
from axiz.pe.sql_agent.services.agent_cache import AgentResponseCache
from axiz.pe.sql_agent.services.llm import StructuredLLM


class IntentRoutingSkill:
    def __init__(self, llm: StructuredLLM, cache: AgentResponseCache | None = None) -> None:
        self.llm = llm
        self.cache = cache

    def _model_profile_projection(self) -> dict[str, Any]:
        registry = getattr(self.llm, "registry", None)
        agent_name = getattr(self.llm, "agent_name", self.llm.__class__.__name__)
        if registry is None or not hasattr(registry, "profile_for"):
            return {"agent": agent_name, "adapter": self.llm.__class__.__name__}
        return registry.profile_for(agent_name).model_dump(mode="json")

    @staticmethod
    def _bounded_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        total = 0
        for item in history[-4:]:
            content = str(item.get("content") or "")[:800]
            remaining = 2400 - total
            if remaining <= 0:
                break
            content = content[:remaining]
            result.append({"role": str(item.get("role") or "unknown")[:24], "content": content})
            total += len(content)
        return result

    async def classify(
        self,
        question: str,
        domains: list[dict],
        history: list[dict[str, str]],
    ) -> IntentDomainOutput:
        normalized_question = " ".join(question.strip().split())
        payload = {
            "contract_version": "intent-domain-v2",
            "question": normalized_question,
            "available_domains": domains,
            "recent_conversation": self._bounded_history(history),
            "model_profile": self._model_profile_projection(),
        }
        if self.cache is not None:
            cached = await self.cache.get("intent-domain", payload)
            if cached.hit and cached.value:
                try:
                    return IntentDomainOutput.model_validate(cached.value)
                except Exception:
                    pass

        system = """
You are the routing agent for a governed enterprise analytics assistant.
Classify the request as analytical_query, catalog_question, capability_question,
conversation_question, or unsupported. For analytical and catalog requests choose exactly one
configured domain when possible. Do not invent domains. Use confidence below 0.70 and provide a
clarification question when multiple domains could reasonably answer the request. This step only
classifies intent and domain; it does not decide investigation complexity or execute tools.
""".strip()
        output = await self.llm.parse(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, default=str),
            response_model=IntentDomainOutput,
        )
        if self.cache is not None and output.confidence >= 0.70:
            await self.cache.set("intent-domain", payload, output.model_dump(mode="json"), ttl_seconds=900)
        return output
