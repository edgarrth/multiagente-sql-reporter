from __future__ import annotations

import json

from axiz.pe.sql_agent.models.contracts import (
    ConversationAnswerOutput,
    ConversationMemory,
)
from axiz.pe.sql_agent.services.llm import StructuredLLM



class ConversationMemorySkill:
    """Answers session questions from structured memory without querying business data."""

    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    async def answer(
        self,
        *,
        question: str,
        history: list[dict[str, str]],
        memory: ConversationMemory,
    ) -> ConversationAnswerOutput:
        if not memory.last_user_request and not history:
            return ConversationAnswerOutput(
                answer=(
                    "Todavía no hay una solicitud analítica anterior en esta conversación. "
                    "Formula una pregunta sobre los datos y conservaré el contexto en esta sesión."
                )
            )

        system = """
You answer questions about the current persisted conversation.
Use the structured session memory as the primary source and the recent conversation only as
support. Never query a database, generate new SQL, or invent facts. Distinguish clearly between
the user's original request, the standalone resolved request, the SQL, filters, period, result,
models, and tokens. Answer in the same language as the user, be concise, and say when a requested
detail is absent.
""".strip()
        payload = {
            "question": question,
            "structured_memory": memory.model_dump(mode="json"),
            "conversation_history": history[-8:],
        }
        return await self.llm.parse(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, default=str),
            response_model=ConversationAnswerOutput,
        )
