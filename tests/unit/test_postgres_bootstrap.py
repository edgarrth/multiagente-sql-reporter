from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_postgres_healthcheck_uses_admin_database_and_api_waits_for_bootstrap() -> None:
    compose = yaml.safe_load(
        (ROOT / "infrastructure" / "docker-compose.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    healthcheck = " ".join(services["postgres"]["healthcheck"]["test"])
    assert "$${POSTGRES_DB}" in healthcheck
    assert "postgres-bootstrap" in services
    assert services["api"]["depends_on"]["postgres-bootstrap"]["condition"] == (
        "service_completed_successfully"
    )
    assert "postgres" not in services["api"]["depends_on"]


def test_postgres_bootstrap_is_idempotent_and_supports_embedded_mode() -> None:
    bootstrap = (
        ROOT / "infrastructure" / "postgres" / "init" / "bootstrap.sh"
    ).read_text(encoding="utf-8")
    assert "BUSINESS_DATA_MODE" in bootstrap
    assert "Synthetic dataset already exists" in bootstrap
    assert "business_schema_version" in bootstrap
    assert "Axiz PostgreSQL bootstrap completed successfully" in bootstrap


def test_business_sql_does_not_reference_control_schema() -> None:
    sql = (
        ROOT
        / "infrastructure"
        / "postgres"
        / "init"
        / "04-analytics-semantic.sql"
    ).read_text(encoding="utf-8")
    assert "SCHEMA operational, analytics, app" not in sql
    assert 'GRANT SELECT ON ALL TABLES IN SCHEMA semantic TO :"reader_role"' in sql
