from __future__ import annotations

import json
import re

from axiz.pe.sql_agent.models.contracts import (
    ContextResolutionOutput,
    ConversationMemory,
)
from axiz.pe.sql_agent.services.llm import StructuredLLM


_FOLLOW_UP_PATTERNS = (
    re.compile(r"^(?:ahora|entonces|adem[aá]s|tambi[eé]n)\b", re.IGNORECASE),
    re.compile(r"^(?:y|pero)\b", re.IGNORECASE),
    re.compile(r"^(?:por|seg[uú]n)\b", re.IGNORECASE),
    re.compile(r"^(?:solo|sin|con)\s+\S+", re.IGNORECASE),
    re.compile(
        r"^(?:aumenta|incrementa|agrega|a[nñ]ade|suma|reduce|quita|elimina|"
        r"cambia|ajusta|sube|baja|extiende|acorta|incluye|excluye)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:comp[aá]ralo|comp[aá]rala|agr[uú]palo|ord[eé]nalo|filtra(?:lo|la)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:el|la|los|las|ese|esa|esos|esas|mismo|misma)\s+"
        r"(?:anterior|resultado|consulta|mes|periodo)\b",
        re.IGNORECASE,
    ),
)

_META_PATTERNS = (
    re.compile(r"(?:qu[eé] puedes hacer|cu[aá]les son tus capacidades|ayuda)", re.IGNORECASE),
    re.compile(
        r"(?:qu[eé]|cual).*\b(?:te ped[ií]|consulta anterior|sql ejecutaste|resultado dio)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?:modelo|tokens?|consumo llm).*\b(?:usaste|gastaste|anterior)\b", re.IGNORECASE),
)


class ContextResolverAgent:
    """Turns elliptical analytical follow-ups into standalone governed questions."""

    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    async def resolve(
        self,
        *,
        question: str,
        memory: ConversationMemory,
        history: list[dict[str, str]],
    ) -> ContextResolutionOutput:
        normalized = " ".join(question.strip().split())

        if self._is_meta_question(normalized) or not self._looks_like_follow_up(normalized):
            return ContextResolutionOutput(
                original_question=question,
                resolved_question=question,
                is_follow_up=False,
                confidence=1.0,
            )

        if not memory.last_resolved_question:
            return ContextResolutionOutput(
                original_question=question,
                resolved_question=question,
                is_follow_up=True,
                confidence=1.0,
                requires_clarification=True,
                clarification_question=(
                    "No tengo una consulta analítica anterior para aplicar ese cambio. "
                    "Indica la métrica, el periodo y la dimensión que deseas consultar."
                ),
            )

        system = """
You resolve follow-up questions for a governed analytics assistant.
Rewrite the current message as one standalone analytical question using only the supplied
structured session memory. Preserve the user's newest instruction and inherit only fields that
are necessary from the previous analytical request. Never invent metrics, filters, dates,
dimensions, domains, entities, or business definitions. Do not generate SQL. If the reference is
ambiguous or cannot be resolved safely, set requires_clarification=true and provide one concise
clarification question. Return inherited_fields using these names when applicable: domain,
metrics, dimensions, filters, time_window, ordering, limit. Answer in the user's language.
""".strip()
        payload = {
            "current_question": question,
            "structured_memory": memory.model_dump(mode="json"),
            "recent_conversation": history[-6:],
        }
        output = await self.llm.parse(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, default=str),
            response_model=ContextResolutionOutput,
        )
        return output.model_copy(
            update={
                "original_question": question,
                "is_follow_up": True,
            }
        )

    @staticmethod
    def _looks_like_follow_up(question: str) -> bool:
        return any(pattern.search(question) for pattern in _FOLLOW_UP_PATTERNS)

    @staticmethod
    def _is_meta_question(question: str) -> bool:
        return any(pattern.search(question) for pattern in _META_PATTERNS)
