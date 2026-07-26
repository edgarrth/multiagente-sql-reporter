from axiz.pe.sql_agent.tools.sql_dialect_normalizer import SqlDialectNormalizer


def test_normalizes_postgres_timezone_and_interval_literal() -> None:
    normalizer = SqlDialectNormalizer("postgres")
    result = normalizer.normalize(
        """
        SELECT *
        FROM semantic.v_merchant_performance
        WHERE metric_date >= CAST(
          DATE_TRUNC('MONTH', CURRENT_TIMESTAMP AT TIME ZONE 'America/Lima')
          - INTERVAL '1 MONTHS' AS DATE
        )
        LIMIT 500;
        """
    )

    assert "TIMEZONE('America/Lima', CURRENT_TIMESTAMP)" in result.sql
    assert "INTERVAL '1' MONTH" in result.sql
    assert not result.sql.endswith(";")
    assert result.changed is True


def test_normalizer_does_not_remove_multiple_statements() -> None:
    normalizer = SqlDialectNormalizer("postgres")
    result = normalizer.normalize("SELECT 1; DELETE FROM x;")

    assert "DELETE FROM x" in result.sql
    assert result.sql.count(";") == 1


def test_normalizes_model_interval_variant() -> None:
    normalizer = SqlDialectNormalizer("postgres")
    result = normalizer.normalize("SELECT CURRENT_DATE - INTERVAL 2 DAYS")

    assert result.sql == "SELECT CURRENT_DATE - INTERVAL '2' DAY"
