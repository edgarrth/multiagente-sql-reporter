from __future__ import annotations

import pytest

from axiz.pe.sql_agent.agents.feedback_interpreter_agent import FeedbackInterpreterAgent
from axiz.pe.sql_agent.models.contracts import (
    SqlChangeType,
    SqlFeedbackStrategy,
)
from axiz.pe.sql_agent.tools.proposal_review_policy import ProposalReviewPolicy
from axiz.pe.sql_agent.tools.sql_feedback import SqlFeedbackApplier
from axiz.pe.sql_agent.tools.sql_feedback_plan import SqlFeedbackPlanValidator


class FailingLLM:
    async def parse(self, **_: object):  # pragma: no cover - must never be called
        raise AssertionError("The LLM must not be called for a pure LIMIT revision")


@pytest.mark.asyncio
async def test_limit_only_follow_up_bypasses_feedback_llm() -> None:
    agent = FeedbackInterpreterAgent(
        FailingLLM(),
        max_result_rows=500,
        plan_validator=SqlFeedbackPlanValidator(),
    )
    plan = await agent.interpret(
        feedback="ponle un limite de 100 registros a la query",
        previous_sql="SELECT merchant_id FROM semantic.v_monthly_payment_metrics LIMIT 20",
        semantic_context={"semantic_symbols": {}},
        current_contract={},
    )

    assert plan.strategy == SqlFeedbackStrategy.AST_ONLY
    assert plan.requires_regeneration is False
    assert len(plan.changes) == 1
    assert plan.changes[0].change_type == SqlChangeType.SET_LIMIT
    assert plan.changes[0].limit == 100
    assert plan.changes[0].deterministic_candidate is True


def test_mixed_feedback_is_not_misclassified_as_limit_only() -> None:
    plan = FeedbackInterpreterAgent._deterministic_limit_only_plan(
        "ponle un limite de 100 registros y cambia el comercio"
    )
    assert plan is None


def test_deterministic_limit_revision_reuses_previous_sql_contract() -> None:
    pytest.importorskip("sqlglot")
    plan = FeedbackInterpreterAgent._deterministic_limit_only_plan(
        "ponle un limite de 100 registros a la query"
    )
    assert plan is not None
    previous = """
        SELECT merchant_id, SUM(total_amount_pen) AS total_amount_pen
        FROM semantic.v_monthly_payment_metrics
        WHERE metric_month >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '2 months'
          AND metric_month < DATE_TRUNC('month', CURRENT_DATE)
        GROUP BY merchant_id
        ORDER BY total_amount_pen DESC
        LIMIT 20
    """
    application = SqlFeedbackApplier("postgres", max_rows=500).apply(
        previous,
        plan,
        previous_sql=previous,
    )

    normalized = application.sql.upper()
    assert "LIMIT 100" in normalized
    assert "ORDER BY TOTAL_AMOUNT_PEN DESC" in normalized
    assert "GROUP BY MERCHANT_ID" in normalized
    assert application.applied_changes == ["change_1"]


def test_ast_only_revision_skips_redundant_llm_self_review() -> None:
    plan = FeedbackInterpreterAgent._deterministic_limit_only_plan("limite 100")
    assert plan is not None
    decision = ProposalReviewPolicy("postgres").evaluate(
        task={"query_mode": "revise_previous"},
        generated_contract={
            "source_objects": ["semantic.a", "semantic.b"],
            "selected_metrics": [],
            "selected_dimensions": [],
        },
        final_sql="SELECT * FROM semantic.a JOIN semantic.b USING (id) LIMIT 100",
        semantic_context={
            "allowed_sources": ["semantic.a", "semantic.b"],
            "projection_metadata": {"fingerprint": "catalog-v1"},
        },
        security_validation={"approved": True},
        cost_validation={
            "approved": True,
            "total_cost": 900,
            "max_plan_cost": 1000,
            "plan_rows": 900,
            "max_plan_rows": 1000,
        },
        feedback_plan=plan.model_dump(mode="json"),
    )

    assert decision.requires_llm_review is False
    assert "deterministic_revision_applied" in decision.checks
    assert decision.reasons == []


def test_specialist_graph_has_deterministic_revision_route() -> None:
    from pathlib import Path

    source = Path(
        "src/axiz/pe/sql_agent/workflow/subgraphs/specialist.py"
    ).read_text(encoding="utf-8")
    assert 'async def apply_deterministic_revision' in source
    assert '("apply_deterministic_revision", apply_deterministic_revision)' in source
    assert 'graph.add_edge("apply_deterministic_revision", "validate_security")' in source


def test_acquiring_budget_is_not_artificially_increased() -> None:
    from pathlib import Path

    source = Path("config/specialists.yaml").read_text(encoding="utf-8")
    acquiring = source.split("  issuing:", 1)[0]
    assert "max_llm_tokens: 24000" in acquiring
