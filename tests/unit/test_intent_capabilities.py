from __future__ import annotations

import pytest

from axiz.pe.sql_agent.agents.intent_domain_agent import IntentDomainAgent
from axiz.pe.sql_agent.models.contracts import Intent


class FailingLLM:
    async def parse(self, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("Capability questions must not call an LLM")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "¿Qué puedes hacer?",
        "que sabes hacer?",
        "Cuáles son tus capacidades?",
        "ayuda",
        "what can you do?",
    ],
)
async def test_capability_question_is_classified_without_llm(question: str) -> None:
    agent = IntentDomainAgent(FailingLLM())  # type: ignore[arg-type]
    result = await agent.classify(question, [], [])
    assert result.intent == Intent.CAPABILITY_QUESTION
    assert result.domain is None
    assert result.confidence == 1.0
