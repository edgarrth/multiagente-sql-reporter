from __future__ import annotations

import json

from axiz.pe.sql_agent.models.contracts import IntentDomainOutput
from axiz.pe.sql_agent.services.llm import StructuredLLM


class IntentDomainAgent:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    async def classify(
        self,
        question: str,
        domains: list[dict],
        history: list[dict[str, str]],
    ) -> IntentDomainOutput:
        system = """
You are the routing agent for a governed enterprise analytics assistant.
Classify the request as analytical_query, catalog_question, or unsupported.
For analytical and catalog requests choose exactly one configured domain when possible.
Do not invent domains. Use confidence below 0.70 and provide a clarification question when
multiple domains could reasonably answer the request. Be concise and deterministic.
""".strip()
        user = json.dumps(
            {
                "question": question,
                "available_domains": domains,
                "recent_conversation": history[-6:],
            },
            ensure_ascii=False,
            default=str,
        )
        return await self.llm.parse(
            system=system,
            user=user,
            response_model=IntentDomainOutput,
        )
