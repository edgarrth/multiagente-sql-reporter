from __future__ import annotations

from pathlib import Path

import pytest

from axiz.pe.sql_agent.models.contracts import SqlFeedbackStrategy
from axiz.pe.sql_agent.skills.sql.feedback_planning import FeedbackPlanningSkill
from axiz.pe.sql_agent.tools.sql_feedback_plan import SqlFeedbackPlanValidator


class UnusedIntentLlm:
    def __init__(self) -> None:
        self.calls = 0

    async def parse(self, **kwargs):  # pragma: no cover
        self.calls += 1
        raise AssertionError("Feedback planning must not make a separate interpretation call")


@pytest.mark.asyncio
async def test_feedback_is_wrapped_as_one_generic_sql_native_revision() -> None:
    llm = UnusedIntentLlm()
    skill = FeedbackPlanningSkill(llm, 500, SqlFeedbackPlanValidator())
    message = "reduce el límite a 100 y quita un mes"
    plan = await skill.interpret(
        feedback=message,
        previous_sql=(
            "SELECT city FROM semantic.v_daily_payment_metrics "
            "WHERE metric_date >= CURRENT_DATE - 60 LIMIT 400"
        ),
        semantic_context={"semantic_symbols": {}},
        current_contract={"limit": 400},
    )

    assert llm.calls == 0
    assert plan.strategy == SqlFeedbackStrategy.REGENERATE
    assert plan.requires_regeneration is True
    assert plan.raw_user_message == message
    assert plan.changes == []
    assert plan.feedback == message


@pytest.mark.asyncio
async def test_projection_and_column_order_feedback_needs_no_new_target_enum() -> None:
    skill = FeedbackPlanningSkill(UnusedIntentLlm(), 500, SqlFeedbackPlanValidator())
    message = "quita amount_pen de la query y que channel se muestre antes que city"
    plan = await skill.interpret(
        feedback=message,
        previous_sql=(
            "SELECT transaction_id, amount_pen, city, channel "
            "FROM semantic.v_payment_transactions LIMIT 50"
        ),
        semantic_context={},
        current_contract={"sources": ["semantic.v_payment_transactions"]},
    )

    assert plan.changes == []
    assert "amount_pen" in plan.raw_user_message
    assert "channel" in plan.raw_user_message


def test_specialist_graph_keeps_governance_after_generic_revision() -> None:
    source = Path("src/axiz/pe/sql_agent/workflow/subgraphs/specialist.py").read_text()
    assert "generate_sql" in source
    assert "validate_security" in source
    assert "estimate_cost" in source
    assert "human" not in source or "HITL" in source or "awaiting_hitl" in source
