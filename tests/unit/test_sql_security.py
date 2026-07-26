import pytest

pytest.importorskip("sqlglot")

from axiz.pe.sql_agent.tools.sql_security import SqlSecurityValidator


ALLOWED = ["semantic.v_daily_payment_metrics"]
POLICY = {
    "required_filter_columns": ["metric_date"],
    "denied_schemas": ["operational", "analytics", "information_schema"],
    "reject_cross_joins": True,
}


def test_allows_bounded_select_and_adds_limit() -> None:
    validator = SqlSecurityValidator("postgres", 500)
    result = validator.validate(
        "SELECT metric_date, approval_rate FROM semantic.v_daily_payment_metrics "
        "WHERE metric_date >= CURRENT_DATE - INTERVAL '7 days'",
        allowed_sources=ALLOWED,
        policy=POLICY,
    )
    assert result.approved
    assert "LIMIT 500" in (result.normalized_sql or "")
    assert result.statement_type == "SELECT"
    assert result.max_rows == 500
    assert result.enforced_limit == 500
    assert result.required_filter_columns == ["metric_date"]
    assert "operational" in result.denied_schemas


def test_blocks_write_statement() -> None:
    validator = SqlSecurityValidator("postgres", 500)
    result = validator.validate(
        "DELETE FROM semantic.v_daily_payment_metrics",
        allowed_sources=ALLOWED,
        policy=POLICY,
    )
    assert not result.approved


def test_blocks_unauthorized_schema() -> None:
    validator = SqlSecurityValidator("postgres", 500)
    result = validator.validate(
        "SELECT * FROM operational.payment_transactions WHERE transaction_date = CURRENT_DATE",
        allowed_sources=ALLOWED,
        policy=POLICY,
    )
    assert not result.approved
    assert any("Unauthorized" in violation for violation in result.violations)
