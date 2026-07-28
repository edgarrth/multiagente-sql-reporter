from __future__ import annotations

import pytest

from axiz.pe.sql_agent.models.contracts import SqlFeedbackStrategy
from axiz.pe.sql_agent.skills.sql.feedback_planning import FeedbackPlanningSkill
from axiz.pe.sql_agent.tools.sql_revision_diff import SqlRevisionDiffAnalyzer


class _UnusedLlm:
    async def parse(self, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("The generic feedback envelope must not make a separate LLM call")


class _PlanValidator:
    def validate(self, plan, semantic_context):
        return plan


@pytest.mark.asyncio
async def test_generic_feedback_keeps_full_message_and_sql_native_regeneration() -> None:
    skill = FeedbackPlanningSkill(_UnusedLlm(), 500, _PlanValidator())
    previous = "SELECT amount_pen, city, channel FROM semantic.v_payment_transactions LIMIT 50"
    message = "quita amount_pen de la query y que channel se muestre antes que city"

    plan = await skill.interpret(
        feedback=message,
        previous_sql=previous,
        semantic_context={},
        current_contract={"sources": ["semantic.v_payment_transactions"]},
    )

    assert plan.strategy == SqlFeedbackStrategy.REGENERATE
    assert plan.raw_user_message == message
    assert plan.changes == []
    assert plan.raw_user_message == message


def test_ast_diff_detects_projection_removal_and_reordering() -> None:
    pytest.importorskip("sqlglot")
    before = """
    SELECT transaction_id, amount_pen, city, channel, response_code
    FROM semantic.v_payment_transactions
    WHERE status = 'REVERSED'
    ORDER BY transaction_id DESC
    LIMIT 50
    """
    after = """
    SELECT transaction_id, channel, city, response_code
    FROM semantic.v_payment_transactions
    WHERE status = 'REVERSED'
    ORDER BY transaction_id DESC
    LIMIT 50
    """
    diff = SqlRevisionDiffAnalyzer().compare(before, after)

    assert diff["parse_valid"] is True
    assert diff["changed"] is True
    assert any("amount_pen" in item for item in diff["projection"]["removed"])
    assert diff["projection"]["after"][1].endswith("channel")
    assert diff["projection"]["after"][2].endswith("city")
    assert diff["where_changed"] is False
    assert diff["order_by_changed"] is False
    assert diff["limit_changed"] is False


def test_sql_snapshot_uses_final_projection_not_stale_contract() -> None:
    pytest.importorskip("sqlglot")
    from axiz.pe.sql_agent.models.query_spec import SemanticQuerySpec, SemanticDimension
    from axiz.pe.sql_agent.services.semantic_query_spec import SemanticQuerySpecService

    base = SemanticQuerySpec(
        spec_id="qs-demo",
        version=4,
        dimensions=[
            SemanticDimension(member="amount_pen", alias="amount_pen"),
            SemanticDimension(member="city", alias="city"),
            SemanticDimension(member="channel", alias="channel"),
        ],
        source_objects=["semantic.v_payment_transactions"],
    )
    sql = (
        "SELECT transaction_id, channel, city "
        "FROM semantic.v_payment_transactions "
        "WHERE status = 'REVERSED' LIMIT 50"
    )
    service = SemanticQuerySpecService()
    snapshot = service.from_sql_snapshot(sql, base=base, raw_user_message="remove amount")
    artifact = service.compile_artifact(
        snapshot, sql,
        source_contracts={
            "semantic.v_payment_transactions": {
                "columns": ["transaction_id", "channel", "city", "status"]
            }
        },
    )

    assert snapshot.version == 5
    assert [item.alias for item in snapshot.dimensions] == [
        "transaction_id", "channel", "city"
    ]
    assert artifact.validation.query_spec_alignment_valid is True
