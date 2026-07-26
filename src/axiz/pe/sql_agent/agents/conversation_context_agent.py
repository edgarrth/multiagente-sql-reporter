from __future__ import annotations

import json
import re

from axiz.pe.sql_agent.models.contracts import ConversationAnswerOutput
from axiz.pe.sql_agent.services.llm import StructuredLLM


_REQUEST_PATTERNS = (
    re.compile(r"(?:qué|que) (?:datos|información|informacion|consulta) .*ped", re.IGNORECASE),
    re.compile(r"(?:qué|que) .*ped[ií]", re.IGNORECASE),
    re.compile(r"(?:consulta|pregunta|solicitud) anterior", re.IGNORECASE),
    re.compile(r"recu[eé]rdame .*?(?:consulta|pregunta|solicitud)", re.IGNORECASE),
)
_SQL_PATTERN = re.compile(r"(?:qué|que) .*?(?:sql|consulta sql).*(?:ejecut|gener|us)", re.IGNORECASE)
_RESULT_PATTERN = re.compile(r"(?:qué|que) resultados?.*(?:dio|obtu|sal)", re.IGNORECASE)
_USAGE_PATTERN = re.compile(r"(?:modelo|tokens?|consumo llm)", re.IGNORECASE)


class ConversationContextAgent:
    """Answers follow-up questions about the persisted chat without querying business data."""

    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    async def answer(
        self,
        *,
        question: str,
        history: list[dict[str, str]],
    ) -> ConversationAnswerOutput:
        if not history:
            return ConversationAnswerOutput(
                answer=(
                    "Todavía no hay una solicitud analítica anterior en esta conversación. "
                    "Formula una pregunta sobre los datos y conservaré el contexto en esta sesión."
                )
            )

        deterministic = self._deterministic_answer(question, history)
        if deterministic:
            return deterministic

        system = """
You answer questions about the current persisted conversation.
Use only the supplied conversation history. Never query a database, generate new SQL, or invent
facts. When the user asks what data they requested, summarize the most recent relevant user
request and its recorded interpretation. When they ask about the previous SQL, result, model,
tokens, assumptions, or decision, use the corresponding recorded assistant context. Distinguish
clearly between what the user requested, what SQL was executed, and what the result showed.
Answer in the same language as the user, be concise, and say when the requested detail is not
present in the history.
""".strip()
        payload = {
            "question": question,
            "conversation_history": history[-12:],
        }
        return await self.llm.parse(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, default=str),
            response_model=ConversationAnswerOutput,
        )

    @staticmethod
    def _deterministic_answer(
        question: str,
        history: list[dict[str, str]],
    ) -> ConversationAnswerOutput | None:
        normalized = " ".join(question.strip().split())
        assistant_contexts = [
            item.get("content", "")
            for item in reversed(history)
            if item.get("role") == "assistant"
        ]

        if any(pattern.search(normalized) for pattern in _REQUEST_PATTERNS):
            for item in reversed(history):
                if item.get("role") != "user":
                    continue
                content = str(item.get("content") or "").strip()
                if not content or any(pattern.search(content) for pattern in _REQUEST_PATTERNS):
                    continue
                interpretation = ConversationContextAgent._latest_prefixed_value(
                    assistant_contexts, "Interpretación registrada:"
                )
                answer = f"Me pediste: **{content}**"
                if interpretation:
                    answer += f"\n\nLa interpretación registrada fue: {interpretation}"
                return ConversationAnswerOutput(
                    answer=answer,
                    referenced_turns=[content],
                )

        if _SQL_PATTERN.search(normalized):
            sql = ConversationContextAgent._latest_prefixed_value(
                assistant_contexts, "SQL ejecutado o propuesto:"
            )
            if sql:
                return ConversationAnswerOutput(
                    answer=f"El SQL más reciente de esta sesión fue:\n\n```sql\n{sql}\n```",
                    referenced_turns=["latest_sql"],
                )

        if _RESULT_PATTERN.search(normalized):
            answer = ConversationContextAgent._latest_prefixed_value(
                assistant_contexts, "Respuesta registrada:"
            )
            if answer:
                return ConversationAnswerOutput(
                    answer=answer,
                    referenced_turns=["latest_result"],
                )

        if _USAGE_PATTERN.search(normalized):
            usage = ConversationContextAgent._latest_prefixed_value(
                assistant_contexts, "Consumo LLM:"
            )
            if usage:
                return ConversationAnswerOutput(
                    answer=f"Consumo LLM de la respuesta anterior: {usage}",
                    referenced_turns=["latest_llm_usage"],
                )
        return None

    @staticmethod
    def _latest_prefixed_value(contexts: list[str], prefix: str) -> str | None:
        for context in contexts:
            for line in context.splitlines():
                if line.startswith(prefix):
                    return line[len(prefix) :].strip()
        return None
