from __future__ import annotations

import json
import re
from typing import Any

from axiz.pe.sql_agent.models.contracts import Intent, IntentDomainOutput
from axiz.pe.sql_agent.services.agent_cache import AgentResponseCache
from axiz.pe.sql_agent.services.llm import StructuredLLM

_CAPABILITY_PATTERNS = (
    re.compile(r"^¿?(?:hola[, ]*)?(?:qué|que) (?:puedes|sabes) hacer\??$", re.IGNORECASE),
    re.compile(r"^¿?(?:cuáles|cuales) son tus capacidades\??$", re.IGNORECASE),
    re.compile(
        r"^¿?(?:muéstrame|muestrame|dime) (?:tus )?(?:capacidades|funciones)\??$",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:help|ayuda|what can you do|capabilities)\??$", re.IGNORECASE),
)

_CONVERSATION_PATTERNS = (
    re.compile(
        r"^¿?(?:qué|que) (?:datos|información|informacion|consulta) (?:te )?(?:pedí|pedi|solicité|solicite)(?: antes| anteriormente)?\??$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^¿?(?:qué|que) (?:te )?(?:pedí|pedi|solicité|solicite)(?: antes| anteriormente)?\??$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^¿?(?:cuál|cual|qué|que) (?:fue|era) (?:la )?(?:consulta|pregunta|solicitud) anterior\??$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^¿?(?:qué|que) (?:sql|consulta sql) (?:ejecutaste|generaste|usaste)\??$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^¿?(?:qué|que) (?:resultado|resultados) (?:dio|obtuviste|obtuvimos|salió|salio)\??$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^¿?(?:recuérdame|recuerdame) (?:la )?(?:consulta|pregunta|solicitud|respuesta) anterior\??$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^¿?(?:sobre qué|sobre que) (?:estamos hablando|era la consulta)\??$",
        re.IGNORECASE,
    ),
)


class IntentDomainAgent:
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
        if any(pattern.fullmatch(normalized_question) for pattern in _CAPABILITY_PATTERNS):
            return IntentDomainOutput(
                intent=Intent.CAPABILITY_QUESTION,
                domain=None,
                confidence=1.0,
                rationale="The user is asking what the assistant can do.",
                clarification_question=None,
            )
        if any(pattern.fullmatch(normalized_question) for pattern in _CONVERSATION_PATTERNS):
            return IntentDomainOutput(
                intent=Intent.CONVERSATION_QUESTION,
                domain=None,
                confidence=1.0,
                rationale="The user is asking about a previous turn in the current session.",
                clarification_question=None,
            )

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
