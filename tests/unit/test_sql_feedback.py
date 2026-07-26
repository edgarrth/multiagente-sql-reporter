import pytest

from axiz.pe.sql_agent.tools.sql_feedback import SqlFeedbackApplier


def test_applies_requested_limit_over_previous_limit() -> None:
    pytest.importorskip("sqlglot")
    tool = SqlFeedbackApplier("postgres", max_rows=500)

    result = tool.apply(
        "SELECT response_code FROM semantic.v_decline_analysis LIMIT 200",
        "dije que subas el límite a 400",
    )

    assert result.requested_limit == 400
    assert result.previous_limit == 200
    assert result.applied_limit == 400
    assert result.changed is True
    assert "LIMIT 400" in result.sql


def test_applies_limit_when_llm_omits_it() -> None:
    pytest.importorskip("sqlglot")
    tool = SqlFeedbackApplier("postgres", max_rows=500)

    result = tool.apply(
        "SELECT response_code FROM semantic.v_decline_analysis",
        "devuelve 350 resultados",
    )

    assert result.applied_limit == 350
    assert "LIMIT 350" in result.sql


def test_clamps_requested_limit_to_governed_maximum() -> None:
    pytest.importorskip("sqlglot")
    tool = SqlFeedbackApplier("postgres", max_rows=500)

    result = tool.apply(
        "SELECT response_code FROM semantic.v_decline_analysis LIMIT 200",
        "cambia el limit a 900",
    )

    assert result.requested_limit == 900
    assert result.applied_limit == 500
    assert "LIMIT 500" in result.sql
    assert result.warnings


def test_unrelated_feedback_does_not_modify_limit() -> None:
    pytest.importorskip("sqlglot")
    tool = SqlFeedbackApplier("postgres", max_rows=500)
    sql = "SELECT response_code FROM semantic.v_decline_analysis LIMIT 200"

    result = tool.apply(sql, "agrupa también por canal")

    assert result.requested_limit is None
    assert result.changed is False
    assert result.sql == sql


def test_reconciles_stale_interpretation_limit() -> None:
    pytest.importorskip("sqlglot")
    tool = SqlFeedbackApplier("postgres", max_rows=500)
    application = tool.apply(
        "SELECT response_code FROM semantic.v_decline_analysis LIMIT 200",
        "sube el límite a 400",
    )

    interpretation = tool.reconcile_interpretation(
        "Principales códigos de rechazo, limitado a 200 resultados.",
        application,
    )

    assert "400" in interpretation
    assert "200" not in interpretation


def test_extracts_limit_from_spanish_feedback_without_sql_parser() -> None:
    assert SqlFeedbackApplier.extract_requested_limit(
        "dije que subas el límite a 400"
    ) == 400


def test_ignores_unrelated_numbers_without_limit_language() -> None:
    assert SqlFeedbackApplier.extract_requested_limit(
        "usa los últimos 6 días y agrupa por canal"
    ) is None
