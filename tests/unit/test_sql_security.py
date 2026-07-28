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


@pytest.mark.parametrize("sql", ["", "   ", "-- only a comment", ";"])
def test_empty_or_comment_only_sql_fails_closed_without_attribute_error(sql: str) -> None:
    validator = SqlSecurityValidator("postgres", 500)
    result = validator.validate(sql, allowed_sources=ALLOWED, policy=POLICY)

    assert result.approved is False
    assert any("empty" in violation.lower() or "non-empty" in violation.lower() for violation in result.violations)


def test_source_contract_rejects_columns_from_another_semantic_view() -> None:
    validator = SqlSecurityValidator("postgres", 500)
    source = "semantic.v_decline_analysis"
    contracts = {
        source: {
            "columns": [
                "metric_date",
                "response_code",
                "declined_count",
                "declined_amount_pen",
            ]
        }
    }
    result = validator.validate(
        "SELECT merchant_name, SUM(declined_count) "
        "FROM semantic.v_decline_analysis "
        "WHERE metric_date >= CURRENT_DATE - 1 GROUP BY merchant_name",
        allowed_sources=[source],
        policy={"required_filter_columns": ["metric_date"]},
        source_contracts=contracts,
    )

    assert result.approved is False
    assert any("merchant_name" in violation for violation in result.violations)


def test_decline_analysis_certified_columns_are_accepted() -> None:
    validator = SqlSecurityValidator("postgres", 500)
    source = "semantic.v_decline_analysis"
    contracts = {
        source: {
            "columns": [
                "metric_date",
                "mcc",
                "city",
                "channel",
                "card_scheme",
                "response_code",
                "declined_count",
                "declined_amount_pen",
            ]
        }
    }
    result = validator.validate(
        "SELECT response_code, SUM(declined_count) AS declined_count "
        "FROM semantic.v_decline_analysis "
        "WHERE metric_date >= (TIMEZONE('America/Lima', CURRENT_TIMESTAMP))::date - 1 "
        "AND metric_date < (TIMEZONE('America/Lima', CURRENT_TIMESTAMP))::date "
        "GROUP BY response_code ORDER BY declined_count DESC",
        allowed_sources=[source],
        policy={"required_filter_columns": ["metric_date"]},
        source_contracts=contracts,
    )

    assert result.approved is True


def test_allows_ordered_top_n_without_date_when_temporal_filter_is_not_enforced() -> None:
    validator = SqlSecurityValidator("postgres", 500)
    source = "semantic.v_payment_transactions"
    result = validator.validate(
        "SELECT transaction_id, transaction_timestamp "
        "FROM semantic.v_payment_transactions "
        "ORDER BY transaction_timestamp DESC LIMIT 20",
        allowed_sources=[source],
        policy={
            "required_filter_columns": ["transaction_date"],
            "enforce_temporal_filter": False,
        },
        source_contracts={
            source: {"columns": ["transaction_id", "transaction_timestamp"]}
        },
    )

    assert result.approved is True
    assert result.enforced_limit == 20


def test_temporal_filter_is_required_only_when_policy_explicitly_enforces_it() -> None:
    validator = SqlSecurityValidator("postgres", 500)
    source = "semantic.v_payment_transactions"
    result = validator.validate(
        "SELECT transaction_id, transaction_timestamp "
        "FROM semantic.v_payment_transactions "
        "ORDER BY transaction_timestamp DESC LIMIT 20",
        allowed_sources=[source],
        policy={
            "required_filter_columns": ["transaction_date"],
            "enforce_temporal_filter": True,
        },
        source_contracts={
            source: {"columns": ["transaction_id", "transaction_timestamp"]}
        },
    )

    assert result.approved is False
    assert any("explicitly enforced temporal filter" in item for item in result.violations)
