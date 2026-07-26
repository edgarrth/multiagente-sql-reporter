from __future__ import annotations

import pytest

from axiz.pe.sql_agent.agents.conversation_context_agent import ConversationContextAgent
from axiz.pe.sql_agent.agents.intent_domain_agent import IntentDomainAgent
from axiz.pe.sql_agent.models.contracts import Intent
from axiz.pe.sql_agent.repositories.session_repository import SessionRepository


class FailingLLM:
    async def parse(self, **kwargs):  # pragma: no cover - deterministic branches only
        raise AssertionError("The deterministic context branch must not call an LLM")


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
async def test_explicit_session_context_question_is_classified_without_llm(
    question: str,
) -> None:
    agent = IntentDomainAgent(FailingLLM())  # type: ignore[arg-type]
    result = await agent.classify(question, [], [])
    assert result.intent == Intent.CONVERSATION_QUESTION
    assert result.domain is None
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_what_data_did_i_request_uses_previous_user_turn_deterministically() -> None:
    agent = ConversationContextAgent(FailingLLM())  # type: ignore[arg-type]
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
    result = await agent.answer(question="¿Qué datos te pedí?", history=history)
    assert "10 comercios" in result.answer
    assert "Interpretación registrada" not in result.answer
    assert "mayor facturación acumulada" in result.answer


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
