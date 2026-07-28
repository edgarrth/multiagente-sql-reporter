import pytest

pytest.importorskip("sqlglot")

from axiz.pe.sql_agent.tools.sql_security import SqlSecurityValidator


ALLOWED = ["semantic.v_daily_payment_metrics"]
POLICY = {
    "denied_schemas": ["operational", "analytics", "information_schema"],
    "reject_cross_joins": True,
}


def test_allows_read_only_query_without_mandatory_clause_and_adds_limit() -> None:
    validator = SqlSecurityValidator("postgres", 500)
    result = validator.validate(
        "SELECT metric_date, approval_rate FROM semantic.v_daily_payment_metrics",
        allowed_sources=ALLOWED,
        policy=POLICY,
    )
    assert result.approved
    assert "LIMIT 500" in (result.normalized_sql or "")
    assert result.enforced_limit == 500
    assert "operational" in result.denied_schemas


def test_blocks_write_statement() -> None:
    result = SqlSecurityValidator("postgres", 500).validate(
        "DELETE FROM semantic.v_daily_payment_metrics",
        allowed_sources=ALLOWED,
        policy=POLICY,
    )
    assert not result.approved


def test_blocks_unauthorized_source() -> None:
    result = SqlSecurityValidator("postgres", 500).validate(
        "SELECT * FROM operational.payment_transactions",
        allowed_sources=ALLOWED,
        policy=POLICY,
    )
    assert not result.approved
    assert any("Unauthorized" in item for item in result.violations)


@pytest.mark.parametrize("sql", ["", "   ", "-- only a comment", ";"])
def test_empty_or_comment_only_sql_fails_closed(sql: str) -> None:
    result = SqlSecurityValidator("postgres", 500).validate(
        sql, allowed_sources=ALLOWED, policy=POLICY
    )
    assert not result.approved


def test_source_contract_rejects_unpublished_column() -> None:
    source = "semantic.v_decline_analysis"
    result = SqlSecurityValidator("postgres", 500).validate(
        "SELECT merchant_name, SUM(declined_count) "
        "FROM semantic.v_decline_analysis GROUP BY merchant_name",
        allowed_sources=[source],
        policy={},
        source_contracts={source: {"columns": ["response_code", "declined_count"]}},
    )
    assert not result.approved
    assert any("merchant_name" in item for item in result.violations)


def test_arbitrary_read_shape_is_allowed_when_catalog_columns_are_valid() -> None:
    source = "semantic.v_payment_transactions"
    result = SqlSecurityValidator("postgres", 500).validate(
        "SELECT transaction_id, transaction_timestamp "
        "FROM semantic.v_payment_transactions "
        "ORDER BY transaction_timestamp DESC LIMIT 20",
        allowed_sources=[source],
        policy={},
        source_contracts={source: {"columns": ["transaction_id", "transaction_timestamp"]}},
    )
    assert result.approved
    assert result.enforced_limit == 20
