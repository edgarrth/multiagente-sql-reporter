from __future__ import annotations

import json
import re

from axiz.pe.sql_agent.models.contracts import (
    ConversationAnswerOutput,
    ConversationMemory,
)
from axiz.pe.sql_agent.services.llm import StructuredLLM


_REQUEST_PATTERNS = (
    re.compile(r"(?:qué|que) (?:datos|información|informacion|consulta) .*ped", re.IGNORECASE),
    re.compile(r"(?:qué|que) .*ped[ií]", re.IGNORECASE),
    re.compile(r"(?:consulta|pregunta|solicitud) anterior", re.IGNORECASE),
    re.compile(r"recu[eé]rdame .*?(?:consulta|pregunta|solicitud)", re.IGNORECASE),
)
_SQL_PATTERN = re.compile(
    r"(?:qué|que) .*?(?:sql|consulta sql).*(?:ejecut|gener|us)",
    re.IGNORECASE,
)
_RESULT_PATTERN = re.compile(r"(?:qué|que) resultados?.*(?:dio|obtu|sal)", re.IGNORECASE)
_USAGE_PATTERN = re.compile(r"(?:modelo|tokens?|consumo llm)", re.IGNORECASE)
_FILTER_PATTERN = re.compile(r"(?:qué|que) filtros?.*(?:usaste|aplicaste|ten[ií]a)", re.IGNORECASE)
_PERIOD_PATTERN = re.compile(
    r"(?:qué|que|cuál|cual) (?:periodo|rango|fecha).*"
    r"(?:usaste|consultaste|aplicaste)",
    re.IGNORECASE,
)


class ConversationContextAgent:
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

        deterministic = self._deterministic_answer(question, memory)
        if deterministic:
            return deterministic

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

    @staticmethod
    def _deterministic_answer(
        question: str,
        memory: ConversationMemory,
    ) -> ConversationAnswerOutput | None:
        normalized = " ".join(question.strip().split())

        if any(pattern.search(normalized) for pattern in _REQUEST_PATTERNS):
            if memory.last_user_request:
                answer = f"Me pediste: **{memory.last_user_request}**"
                if (
                    memory.last_resolved_question
                    and memory.last_resolved_question != memory.last_user_request
                ):
                    answer += (
                        "\n\nLa solicitud autocontenida usada por el agente fue: "
                        f"{memory.last_resolved_question}"
                    )
                if memory.last_interpretation:
                    answer += f"\n\nLa interpretación registrada fue: {memory.last_interpretation}"
                return ConversationAnswerOutput(
                    answer=answer,
                    referenced_turns=["last_user_request"],
                )

        if _SQL_PATTERN.search(normalized) and memory.last_sql:
            return ConversationAnswerOutput(
                answer=f"El SQL más reciente de esta sesión fue:\n\n```sql\n{memory.last_sql}\n```",
                referenced_turns=["last_sql"],
            )

        if _RESULT_PATTERN.search(normalized):
            if memory.last_answer:
                detail = memory.last_answer
                if memory.last_row_count is not None:
                    detail += f"\n\nEl resultado contenía {memory.last_row_count} filas."
                return ConversationAnswerOutput(
                    answer=detail,
                    referenced_turns=["last_answer", "last_row_count"],
                )

        if _FILTER_PATTERN.search(normalized) and memory.last_filters:
            filters = "; ".join(
                f"{item.field} {item.operator} {item.value}" for item in memory.last_filters
            )
            return ConversationAnswerOutput(
                answer=f"Los filtros registrados fueron: {filters}.",
                referenced_turns=["last_filters"],
            )

        if _PERIOD_PATTERN.search(normalized) and memory.last_time_window:
            window = memory.last_time_window
            parts = [window.label] if window.label else []
            if window.start_expression:
                parts.append(f"desde {window.start_expression}")
            if window.end_expression:
                parts.append(f"hasta {window.end_expression}")
            return ConversationAnswerOutput(
                answer="El periodo registrado fue: " + ", ".join(parts) + ".",
                referenced_turns=["last_time_window"],
            )

        if _USAGE_PATTERN.search(normalized) and (
            memory.last_models or memory.last_token_usage is not None
        ):
            models = ", ".join(memory.last_models) or "no registrado"
            tokens = (
                memory.last_token_usage
                if memory.last_token_usage is not None
                else "no registrado"
            )
            return ConversationAnswerOutput(
                answer=f"Modelos: {models}. Tokens consumidos: {tokens}.",
                referenced_turns=["last_models", "last_token_usage"],
            )
        return None
