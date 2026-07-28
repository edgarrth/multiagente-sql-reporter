from __future__ import annotations

import pytest

from axiz.pe.sql_agent.skills.coordinator.intent_routing import IntentRoutingSkill
from axiz.pe.sql_agent.models.contracts import Intent, IntentDomainOutput


class SemanticLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def parse(self, **kwargs):
        self.calls += 1
        return IntentDomainOutput(
            intent=Intent.CAPABILITY_QUESTION,
            domain=None,
            confidence=1.0,
            rationale="The user asks what the assistant can do.",
        )


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
async def test_capability_question_is_classified_by_semantic_router(question: str) -> None:
    llm = SemanticLLM()
    agent = IntentRoutingSkill(llm)  # type: ignore[arg-type]
    result = await agent.classify(question, [], [])
    assert result.intent == Intent.CAPABILITY_QUESTION
    assert result.domain is None
    assert result.confidence == 1.0
    assert llm.calls == 1
