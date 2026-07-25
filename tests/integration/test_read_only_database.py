import os

import pytest

psycopg = pytest.importorskip("psycopg")


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_AGENT_DSN"),
    reason="Set TEST_AGENT_DSN to run PostgreSQL integration tests",
)


def test_semantic_dataset_contains_realistic_volume() -> None:
    with psycopg.connect(os.environ["TEST_AGENT_DSN"]) as connection:
        transaction_count, merchant_count = connection.execute(
            """
            SELECT count(*), count(DISTINCT merchant_id)
            FROM semantic.v_payment_transactions
            WHERE transaction_date >= CURRENT_DATE - INTERVAL '365 days'
            """
        ).fetchone()
        assert transaction_count > 200_000
        assert merchant_count == 250


def test_semantic_views_cover_payments_declines_and_chargebacks() -> None:
    with psycopg.connect(os.environ["TEST_AGENT_DSN"]) as connection:
        daily_rows = connection.execute(
            "SELECT count(*) FROM semantic.v_daily_payment_metrics"
        ).fetchone()[0]
        decline_rows = connection.execute(
            "SELECT count(*) FROM semantic.v_decline_analysis"
        ).fetchone()[0]
        chargeback_rows = connection.execute(
            "SELECT count(*) FROM semantic.v_chargeback_metrics"
        ).fetchone()[0]
        assert daily_rows > 0
        assert decline_rows > 0
        assert chargeback_rows > 0


def test_agent_role_cannot_modify_or_read_internal_layers() -> None:
    with psycopg.connect(os.environ["TEST_AGENT_DSN"]) as connection:
        with pytest.raises(psycopg.Error):
            connection.execute("SELECT * FROM operational.payment_transactions LIMIT 1")
        connection.rollback()

        with pytest.raises(psycopg.Error):
            connection.execute("SELECT * FROM analytics.fact_payment_transactions LIMIT 1")
        connection.rollback()

        with pytest.raises(psycopg.Error):
            connection.execute("CREATE TABLE semantic.forbidden(id integer)")
