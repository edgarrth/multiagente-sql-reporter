from __future__ import annotations

import json
import re

from axiz.pe.sql_agent.models.contracts import Intent, IntentDomainOutput
from axiz.pe.sql_agent.services.llm import StructuredLLM


_CAPABILITY_PATTERNS = (
    re.compile(
        r"^¿?(?:hola[, ]*)?(?:qué|que) (?:puedes|sabes) hacer\??$",
        re.IGNORECASE,
    ),
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
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

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

        system = """
You are the routing agent for a governed enterprise analytics assistant.
Classify the request as analytical_query, catalog_question, capability_question, conversation_question, or unsupported.
Capability questions ask what the assistant can do and do not require a domain.
Conversation questions ask about a previous request, SQL, result, model, token usage, or decision
in the current session and do not require a domain or a new SQL execution.
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
