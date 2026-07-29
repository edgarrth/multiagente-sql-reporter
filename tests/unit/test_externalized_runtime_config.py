from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from axiz.pe.sql_agent.config import Settings


ROOT = Path(__file__).resolve().parents[2]


def required_settings(**overrides):
    values = {
        "app_secret_key": "a" * 40,
        "bootstrap_username": "admin",
        "bootstrap_password": "StrongBootstrapPassword123!",
        "bootstrap_roles": ["admin", "analyst"],
        "internal_service_key": "i" * 32,
        "database_url": "postgresql+psycopg://owner:pwd@db/control",
        "checkpoint_database_url": "postgresql://owner:pwd@db/control",
        "agent_database_url": "postgresql://reader:pwd@db/business",
        "redis_url": "redis://redis:6379/0",
        "cors_origins": ["http://localhost:8501"],
    }
    values.update(overrides)
    return values


def test_sensitive_settings_have_no_source_code_fallbacks(monkeypatch) -> None:
    for key in (
        "APP_SECRET_KEY",
        "BOOTSTRAP_USERNAME",
        "BOOTSTRAP_PASSWORD",
        "BOOTSTRAP_ROLES",
        "INTERNAL_SERVICE_KEY",
        "DATABASE_URL",
        "CHECKPOINT_DATABASE_URL",
        "AGENT_DATABASE_URL",
        "REDIS_URL",
        "CORS_ORIGINS",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_placeholder_secrets_are_rejected() -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        Settings(**required_settings(app_secret_key="change-me-" + "x" * 32))


def test_bootstrap_roles_are_externalized() -> None:
    settings = Settings(**required_settings(bootstrap_roles=["analyst", "admin", "analyst"]))
    assert settings.bootstrap_roles == ["analyst", "admin"]
    auth_source = (
        ROOT / "src/axiz/pe/sql_agent/services/auth_service.py"
    ).read_text(encoding="utf-8")
    assert "roles=self.settings.bootstrap_roles" in auth_source


def test_env_template_does_not_publish_reusable_passwords() -> None:
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    for key in (
        "APP_SECRET_KEY",
        "BOOTSTRAP_PASSWORD",
        "INTERNAL_SERVICE_KEY",
        "POSTGRES_PASSWORD",
        "AGENT_READER_PASSWORD",
        "DATABASE_URL",
        "CHECKPOINT_DATABASE_URL",
        "AGENT_DATABASE_URL",
    ):
        assert values[key] == ""


def test_compose_injects_runtime_config_without_interpolating_secrets() -> None:
    compose_text = (ROOT / "infrastructure/docker-compose.yml").read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)

    for forbidden in (
        "${DATABASE_URL:?",
        "${CHECKPOINT_DATABASE_URL:?",
        "${AGENT_DATABASE_URL:?",
        "${APP_SECRET_KEY:?",
        "${BOOTSTRAP_PASSWORD:?",
        "${POSTGRES_PASSWORD:?",
        "${AGENT_READER_PASSWORD:?",
    ):
        assert forbidden not in compose_text

    for service_name in ("postgres", "postgres-bootstrap", "redis", "api", "streamlit"):
        assert "../.env" in compose["services"][service_name]["env_file"]

    assert "environment" not in compose["services"]["api"]
    assert "environment" not in compose["services"]["streamlit"]


def test_generator_repairs_blank_urls_and_preserves_existing_api_key(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    env_path.write_text(
        template.replace("OPENAI_API_KEY=", "OPENAI_API_KEY=existing-provider-key", 1),
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/generate_local_env.py"),
            "--output",
            str(env_path),
        ],
        check=True,
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )

    values = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value

    assert values["OPENAI_API_KEY"] == "existing-provider-key"
    assert values["APP_SECRET_KEY"]
    assert values["POSTGRES_PASSWORD"]
    assert values["AGENT_READER_PASSWORD"]
    assert values["DATABASE_URL"].startswith("postgresql+psycopg://")
    assert values["CHECKPOINT_DATABASE_URL"].startswith("postgresql://")
    assert values["AGENT_DATABASE_URL"].startswith("postgresql://")
    assert values["REDIS_URL"] == "redis://redis:6379/0"

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/validate_env.py"),
            "--env-file",
            str(env_path),
        ],
        check=True,
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
