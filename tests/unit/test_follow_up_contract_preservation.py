from pathlib import Path

import pytest

from axiz.pe.sql_agent.models.contracts import (
    FeedbackSemanticComplianceOutput,
    SqlChangeRequest,
    SqlChangeType,
    SqlFeedbackPlan,
    SqlFeedbackStrategy,
    SqlGenerationOutput,
)
from axiz.pe.sql_agent.tools.sql_feedback import SqlFeedbackApplier
from axiz.pe.sql_agent.tools.sql_feedback_compliance import SqlFeedbackComplianceValidator


def _time_window_plan() -> SqlFeedbackPlan:
    return SqlFeedbackPlan(
        feedback="aumenta un mes más a la consulta",
        summary="Amplía el periodo en un mes",
        strategy=SqlFeedbackStrategy.REGENERATE,
        changes=[
            SqlChangeRequest(
                change_id="change_1",
                change_type=SqlChangeType.CHANGE_TIME_WINDOW,
                value="añadir un mes al periodo anterior",
            )
        ],
    )


def test_context_resolver_uses_semantic_relation_instead_of_phrase_rules() -> None:
    source = Path(
        "src/axiz/pe/sql_agent/agents/context_resolver_agent.py"
    ).read_text(encoding="utf-8")
    assert "ContextRelation.ANALYTICAL_FOLLOW_UP" in source
    assert "_FOLLOW_UP_PATTERNS" not in source
    assert "_looks_like_follow_up" not in source


def test_regenerated_follow_up_preserves_unrequested_limit_and_order() -> None:
    pytest.importorskip("sqlglot")
    previous = """
        SELECT channel,
               CAST(SUM(approved_count) AS DECIMAL) / NULLIF(SUM(transaction_count), 0) AS approval_rate
        FROM semantic.v_monthly_payment_metrics
        WHERE metric_month >= DATE_TRUNC('MONTH', CURRENT_DATE) - INTERVAL '2 MONTHS'
          AND metric_month < DATE_TRUNC('MONTH', CURRENT_DATE)
        GROUP BY channel
        ORDER BY channel
        LIMIT 300
    """
    regenerated = """
        SELECT channel,
               CAST(SUM(approved_count) AS DECIMAL) / NULLIF(SUM(transaction_count), 0) AS approval_rate
        FROM semantic.v_monthly_payment_metrics
        WHERE metric_month >= DATE_TRUNC('MONTH', CURRENT_DATE) - INTERVAL '3 MONTHS'
          AND metric_month < DATE_TRUNC('MONTH', CURRENT_DATE)
        GROUP BY channel
        ORDER BY channel DESC
        LIMIT 500
    """
    result = SqlFeedbackApplier("postgres", max_rows=500).apply(
        regenerated,
        _time_window_plan(),
        previous_sql=previous,
    )
    normalized = result.sql.upper()
    assert "INTERVAL '3 MONTHS'" in normalized
    assert "LIMIT 300" in normalized
    assert "ORDER BY CHANNEL" in normalized
    assert "ORDER BY CHANNEL DESC" not in normalized
    assert "limit" in result.preserved_invariants
    assert "ordering" in result.preserved_invariants


def test_compliance_rejects_unrequested_limit_change() -> None:
    pytest.importorskip("sqlglot")
    previous = "SELECT channel FROM semantic.v_monthly_payment_metrics LIMIT 300"
    final = "SELECT channel FROM semantic.v_monthly_payment_metrics LIMIT 500"
    generated = SqlGenerationOutput(
        sql=final,
        interpretation="Amplía el periodo",
        selected_dimensions=["channel"],
        source_objects=["semantic.v_monthly_payment_metrics"],
    )
    semantic = FeedbackSemanticComplianceOutput(
        compliant=True,
        applied_changes=["change_1"],
        confidence=1.0,
        rationale="El periodo fue ampliado.",
    )
    application = SqlFeedbackApplier("postgres", 500).apply(final, _time_window_plan())
    result = SqlFeedbackComplianceValidator("postgres").validate(
        plan=_time_window_plan(),
        previous_sql=previous,
        final_sql=final,
        generated=generated,
        application=application,
        semantic=semantic,
    )
    assert result.compliant is False
    assert "se modificó LIMIT sin solicitarlo" in result.unexpected_changes


def test_graph_routes_analytical_follow_ups_through_change_pipeline() -> None:
    graph = Path("src/axiz/pe/sql_agent/workflow/graph.py").read_text(encoding="utf-8")
    nodes = Path("src/axiz/pe/sql_agent/workflow/nodes.py").read_text(encoding="utf-8")
    assert 'graph.add_node("interpret_follow_up"' in graph
    assert '"interpret_follow_up": "interpret_follow_up"' in graph
    assert "async def interpret_follow_up" in nodes
    assert 'previous_sql=state.get("previous_review_sql")' in nodes
