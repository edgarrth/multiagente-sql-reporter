import pytest

from axiz.pe.sql_agent.models.contracts import (
    FeedbackSemanticComplianceOutput,
    SqlChangeRequest,
    SqlChangeType,
    SqlFeedbackPlan,
    SqlFeedbackStrategy,
    SqlGenerationOutput,
    SqlSortDirection,
)
from axiz.pe.sql_agent.tools.sql_feedback import SqlFeedbackApplier
from axiz.pe.sql_agent.tools.sql_feedback_compliance import SqlFeedbackComplianceValidator


def _plan(*changes: SqlChangeRequest, strategy: SqlFeedbackStrategy = SqlFeedbackStrategy.HYBRID):
    return SqlFeedbackPlan(
        feedback="aplica los cambios",
        summary="Plan compuesto",
        strategy=strategy,
        changes=list(changes),
    )


def test_applies_compound_limit_filter_and_order_feedback() -> None:
    pytest.importorskip("sqlglot")
    tool = SqlFeedbackApplier("postgres", max_rows=500)
    plan = _plan(
        SqlChangeRequest(
            change_id="limit",
            change_type=SqlChangeType.SET_LIMIT,
            limit=400,
            deterministic_candidate=True,
        ),
        SqlChangeRequest(
            change_id="city",
            change_type=SqlChangeType.ADD_FILTER,
            target="city",
            operator="=",
            value="Lima",
            deterministic_candidate=True,
        ),
        SqlChangeRequest(
            change_id="order",
            change_type=SqlChangeType.CHANGE_ORDER,
            target="declined_count",
            direction=SqlSortDirection.ASC,
            deterministic_candidate=True,
        ),
    )
    result = tool.apply(
        "SELECT response_code, SUM(declined_count) AS declined_count "
        "FROM semantic.v_decline_analysis WHERE metric_date >= CURRENT_DATE - INTERVAL '6 DAYS' "
        "GROUP BY response_code ORDER BY declined_count DESC LIMIT 200",
        plan,
    )
    normalized = result.sql.upper()
    assert "LIMIT 400" in normalized
    assert "CITY = 'LIMA'" in normalized
    assert "ORDER BY DECLINED_COUNT ASC" in normalized
    assert set(result.applied_changes) == {"limit", "city", "order"}


def test_replaces_and_removes_filters_and_updates_contract() -> None:
    pytest.importorskip("sqlglot")
    tool = SqlFeedbackApplier("postgres", max_rows=500)
    plan = _plan(
        SqlChangeRequest(
            change_id="replace_city",
            change_type=SqlChangeType.REPLACE_FILTER,
            target="city",
            previous_target="city",
            previous_value="Arequipa",
            operator="=",
            value="Lima",
        ),
        SqlChangeRequest(
            change_id="remove_channel",
            change_type=SqlChangeType.REMOVE_FILTER,
            target="channel",
        ),
    )
    result = tool.apply(
        "SELECT city, channel, SUM(processed_amount_pen) amount "
        "FROM semantic.v_daily_payment_metrics "
        "WHERE city = 'Arequipa' AND channel = 'POS' GROUP BY city, channel",
        plan,
    )
    assert "Lima" in result.sql
    assert "Arequipa" not in result.sql
    assert "channel =" not in result.sql.lower()
    reconciled = tool.reconcile_filters(
        [
            {"field": "city", "operator": "=", "value": "Arequipa", "source": "user"},
            {"field": "channel", "operator": "=", "value": "POS", "source": "user"},
        ],
        plan,
    )
    assert reconciled == [
        {"field": "city", "operator": "=", "value": "Lima", "source": "human_feedback"}
    ]


def test_compliance_detects_missing_semantic_change() -> None:
    pytest.importorskip("sqlglot")
    plan = _plan(
        SqlChangeRequest(
            change_id="add_channel",
            change_type=SqlChangeType.ADD_DIMENSION,
            target="channel",
        )
    )
    generated = SqlGenerationOutput(
        sql="SELECT mcc, SUM(processed_amount_pen) amount FROM semantic.v_daily_payment_metrics GROUP BY mcc",
        interpretation="Monto por MCC",
        selected_metrics=["processed_amount_pen"],
        selected_dimensions=["mcc"],
        source_objects=["semantic.v_daily_payment_metrics"],
    )
    application = SqlFeedbackApplier("postgres", 500).apply(generated.sql, plan)
    semantic = FeedbackSemanticComplianceOutput(
        compliant=False,
        missing_changes=["add_channel"],
        confidence=1.0,
        rationale="channel no fue agregado",
    )
    result = SqlFeedbackComplianceValidator("postgres").validate(
        plan=plan,
        previous_sql=generated.sql,
        final_sql=application.sql,
        generated=generated,
        application=application,
        semantic=semantic,
    )
    assert result.compliant is False
    assert "add_channel" in result.missing_changes
    assert result.retry_instruction


def test_graph_contains_hybrid_feedback_pipeline() -> None:
    from pathlib import Path

    graph = Path("src/axiz/pe/sql_agent/workflow/graph.py").read_text(encoding="utf-8")
    nodes = Path("src/axiz/pe/sql_agent/workflow/nodes.py").read_text(encoding="utf-8")
    assert 'graph.add_node("interpret_feedback"' in graph
    assert 'graph.add_node("apply_feedback"' in graph
    assert 'graph.add_node("validate_feedback_compliance"' in graph
    assert "FeedbackInterpreterAgent" in nodes
    assert "FeedbackComplianceAgent" in nodes
    assert "SqlFeedbackComplianceValidator" in nodes


def test_plan_validator_normalizes_catalog_targets_and_strategy() -> None:
    from axiz.pe.sql_agent.tools.sql_feedback_plan import SqlFeedbackPlanValidator

    plan = _plan(
        SqlChangeRequest(
            change_id="city",
            change_type=SqlChangeType.ADD_FILTER,
            target="Ciudad",
            operator="=",
            value="Lima",
        ),
        SqlChangeRequest(
            change_id="metric",
            change_type=SqlChangeType.ADD_METRIC,
            target="facturación",
        ),
    )
    context = {
        "semantic_symbols": {
            "dimensions": [
                {"name": "city", "column": "city", "synonyms": ["Ciudad"]}
            ],
            "metrics": [
                {
                    "name": "processed_amount_pen",
                    "column": "processed_amount_pen",
                    "synonyms": ["facturación"],
                }
            ],
            "sources": [],
        }
    }
    result = SqlFeedbackPlanValidator().validate(plan, context)
    assert result.strategy == SqlFeedbackStrategy.HYBRID
    assert result.changes[0].target == "city"
    assert result.changes[1].target == "processed_amount_pen"
    assert result.requires_clarification is False


def test_plan_validator_requires_clarification_for_unknown_symbol() -> None:
    from axiz.pe.sql_agent.tools.sql_feedback_plan import SqlFeedbackPlanValidator

    plan = _plan(
        SqlChangeRequest(
            change_id="unknown",
            change_type=SqlChangeType.ADD_DIMENSION,
            target="unpublished_dimension",
        )
    )
    result = SqlFeedbackPlanValidator().validate(
        plan,
        {"semantic_symbols": {"dimensions": [], "metrics": [], "sources": []}},
    )
    assert result.requires_clarification is True
    assert result.strategy == SqlFeedbackStrategy.CLARIFICATION
    assert "unpublished_dimension" in (result.clarification_question or "")
