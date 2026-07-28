from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from axiz.pe.sql_agent.models.contracts import (
    ContextRelation,
    ContextResolutionOutput,
    ConversationMemory,
)
from axiz.pe.sql_agent.skills.coordinator.context_resolution import ContextResolutionSkill


class _NeverCalledLlm:
    async def parse(self, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("A first standalone request must bypass dependency resolution")


class _FollowUpLlm:
    agent_name = "investigation_coordinator"

    async def parse(self, **kwargs):
        return ContextResolutionOutput(
            original_question="",
            resolved_question=(
                "Dame las 20 últimas transacciones, ordenadas desde la más reciente, "
                "sin inventar un filtro de estado ni un rango temporal."
            ),
            relation=ContextRelation.ANALYTICAL_FOLLOW_UP,
            confidence=0.99,
            rationale="The prior failed request and the correction form one complete objective.",
        )


@pytest.mark.asyncio
async def test_first_top_n_request_is_always_routed_as_independent() -> None:
    skill = ContextResolutionSkill(_NeverCalledLlm())
    question = "Dame las 20 últimas transacciones ejecutadas"

    output = await skill.resolve(
        question=question,
        memory=ConversationMemory(),
        history=[{"role": "user", "content": question}],
    )

    assert output.relation == ContextRelation.INDEPENDENT_REQUEST
    assert output.resolved_question == question
    assert output.requires_clarification is False
    assert output.requires_sql_revision is False


@pytest.mark.asyncio
async def test_follow_up_after_failed_attempt_can_become_fresh_generation() -> None:
    skill = ContextResolutionSkill(_FollowUpLlm())

    output = await skill.resolve(
        question="dije las últimas 20 por lo tanto cuenta desde hoy hasta que consigas 20",
        memory=ConversationMemory(),
        history=[
            {"role": "user", "content": "Dame las 20 últimas transacciones ejecutadas"},
            {
                "role": "assistant",
                "content": "¿Qué rango de fechas explícito debo usar?",
            },
            {
                "role": "user",
                "content": "dije las últimas 20 por lo tanto cuenta desde hoy hasta que consigas 20",
            },
        ],
    )

    assert output.relation == ContextRelation.ANALYTICAL_FOLLOW_UP
    assert output.requires_clarification is False
    assert output.requires_sql_revision is False
    assert "20 últimas transacciones" in output.resolved_question


def test_generation_prompt_does_not_force_an_arbitrary_date_range() -> None:
    source = Path("src/axiz/pe/sql_agent/skills/sql/generation.py").read_text(
        encoding="utf-8"
    )
    assert "Always bound transaction data by date" not in source
    assert "Do not invent a temporal predicate" in source
    assert "For latest/top-N records" in source
    assert "Do not ask for dates" in source


def test_catalog_policy_allows_ordered_top_n_without_time_filter() -> None:
    path = Path("semantic_catalog/domains/acquiring/domain.yaml")
    policy = yaml.safe_load(path.read_text(encoding="utf-8"))["query_policy"]

    assert policy["enforce_temporal_filter"] is False
    assert policy["allow_ordered_top_n_without_time_filter"] is True
    assert "required_filter_columns" not in policy


def test_clarification_message_is_not_hardcoded_as_temporal() -> None:
    source = Path("src/axiz/pe/sql_agent/workflow/nodes.py").read_text(
        encoding="utf-8"
    )
    assert "el cambio temporal es ambiguo" not in source
    assert "la solicitud requiere una aclaración" in source
