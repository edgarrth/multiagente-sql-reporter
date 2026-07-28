from __future__ import annotations

import pytest

from axiz.pe.sql_agent.models.contracts import (
    SqlChangeRequest,
    SqlChangeType,
    SqlFeedbackPlan,
    SqlFeedbackStrategy,
)
from axiz.pe.sql_agent.tools.sql_feedback import SqlFeedbackApplier


def _limit_plan(value: int) -> SqlFeedbackPlan:
    return SqlFeedbackPlan(
        feedback=f"set limit {value}",
        summary=f"Set row limit to {value}",
        strategy=SqlFeedbackStrategy.AST_ONLY,
        changes=[
            SqlChangeRequest(
                change_id="change_1",
                change_type=SqlChangeType.SET_LIMIT,
                limit=value,
                deterministic_candidate=True,
            )
        ],
        requires_regeneration=False,
    )


def test_applies_requested_limit_over_previous_limit() -> None:
    pytest.importorskip("sqlglot")
    tool = SqlFeedbackApplier("postgres", max_rows=500)
    result = tool.apply(
        "SELECT response_code FROM semantic.v_decline_analysis LIMIT 200",
        _limit_plan(400),
    )
    assert result.requested_limit == 400
    assert result.previous_limit == 200
    assert result.applied_limit == 400
    assert result.changed is True
    assert "LIMIT 400" in result.sql


def test_applies_limit_when_sql_omits_it() -> None:
    pytest.importorskip("sqlglot")
    tool = SqlFeedbackApplier("postgres", max_rows=500)
    result = tool.apply(
        "SELECT response_code FROM semantic.v_decline_analysis",
        _limit_plan(350),
    )
    assert result.applied_limit == 350
    assert "LIMIT 350" in result.sql


def test_clamps_requested_limit_to_governed_maximum() -> None:
    pytest.importorskip("sqlglot")
    tool = SqlFeedbackApplier("postgres", max_rows=500)
    result = tool.apply(
        "SELECT response_code FROM semantic.v_decline_analysis LIMIT 200",
        _limit_plan(900),
    )
    assert result.requested_limit == 900
    assert result.applied_limit == 500
    assert "LIMIT 500" in result.sql
    assert result.warnings


def test_raw_natural_language_is_not_parsed_by_ast_service() -> None:
    tool = SqlFeedbackApplier("postgres", max_rows=500)
    result = tool.apply(
        "SELECT response_code FROM semantic.v_decline_analysis LIMIT 200",
        "sube el límite a 400",
    )
    assert result.requested_limit is None
    assert result.changed is False
    assert result.sql.endswith("LIMIT 200")


def test_reconciles_interpretation_from_verified_application() -> None:
    pytest.importorskip("sqlglot")
    tool = SqlFeedbackApplier("postgres", max_rows=500)
    application = tool.apply(
        "SELECT response_code FROM semantic.v_decline_analysis LIMIT 200",
        _limit_plan(400),
    )
    interpretation = tool.reconcile_interpretation(
        "Principales códigos de rechazo, limitado a 200 resultados.",
        application,
    )
    assert "400" in interpretation
