from __future__ import annotations

import pytest

from axiz.pe.sql_agent.skills.coordinator.conversation_memory import ConversationMemorySkill
from axiz.pe.sql_agent.skills.coordinator.intent_routing import IntentRoutingSkill
from axiz.pe.sql_agent.models.contracts import (
    ConversationAnswerOutput,
    ConversationMemory,
    Intent,
    IntentDomainOutput,
)
from axiz.pe.sql_agent.repositories.session_repository import SessionRepository


class SemanticLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def parse(self, *, response_model, **kwargs):
        self.calls += 1
        if response_model is IntentDomainOutput:
            return IntentDomainOutput(
                intent=Intent.CONVERSATION_QUESTION,
                domain=None,
                confidence=1.0,
                rationale="The message asks about the existing session state.",
            )
        if response_model is ConversationAnswerOutput:
            return ConversationAnswerOutput(
                answer=(
                    "Pediste los 10 comercios con mayor facturación acumulada "
                    "de los últimos dos meses."
                ),
                referenced_turns=["last_user_request", "last_interpretation"],
            )
        raise AssertionError(f"Unexpected response model: {response_model}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "¿Qué datos te pedí?",
        "que te pedi?",
        "Cuál fue la consulta anterior?",
        "Recuérdame la solicitud anterior",
    ],
)
async def test_explicit_session_context_question_is_classified_semantically(
    question: str,
) -> None:
    llm = SemanticLLM()
    agent = IntentRoutingSkill(llm)  # type: ignore[arg-type]
    result = await agent.classify(question, [], [])
    assert result.intent == Intent.CONVERSATION_QUESTION
    assert result.domain is None
    assert result.confidence == 1.0
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_what_data_did_i_request_uses_structured_memory_context() -> None:
    llm = SemanticLLM()
    agent = ConversationMemorySkill(llm)  # type: ignore[arg-type]
    history = [
        {
            "role": "user",
            "content": "Dame los 10 comercios con mayor facturación de los últimos dos meses",
        },
        {
            "role": "assistant",
            "content": (
                "Respuesta previa\n"
                "Interpretación registrada: Los 10 comercios con mayor facturación acumulada.\n"
                "SQL ejecutado o propuesto: SELECT merchant_id FROM semantic.v_merchant_performance"
            ),
        },
    ]
    result = await agent.answer(
        question="¿Qué datos te pedí?",
        history=history,
        memory=ConversationMemory(
            last_user_request=(
                "Dame los 10 comercios con mayor facturación de los últimos dos meses"
            ),
            last_interpretation="Los 10 comercios con mayor facturación acumulada.",
            last_sql="SELECT merchant_id FROM semantic.v_merchant_performance",
        ),
    )
    assert "10 comercios" in result.answer
    assert "Interpretación registrada" not in result.answer
    assert "mayor facturación acumulada" in result.answer
    assert llm.calls == 1


def test_persisted_assistant_payload_is_enriched_for_session_memory() -> None:
    content = SessionRepository._context_content(
        "assistant",
        "Los comercios con mayor facturación fueron A y B.",
        {
            "payload": {
                "interpretation": "Top comercios por monto procesado",
                "sql": "SELECT merchant_name\nFROM semantic.v_merchant_performance",
                "answer": "Los comercios con mayor facturación fueron A y B.",
                "result": {
                    "columns": ["merchant_name", "processed_amount_pen"],
                    "rows": [{"merchant_name": "A", "processed_amount_pen": 100}],
                    "row_count": 10,
                },
                "llm_usage": {
                    "call_count": 3,
                    "actual_total_tokens": 1200,
                    "calls": [{"model": "gpt-test"}],
                },
            }
        },
    )
    assert "Interpretación registrada: Top comercios" in content
    assert "SQL ejecutado o propuesto: SELECT merchant_name FROM" in content
    assert "Resultado SQL: 10 filas" in content
    assert "Consumo LLM:" in content
