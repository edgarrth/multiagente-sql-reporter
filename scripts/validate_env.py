#!/usr/bin/env python3
"""Validate local environment variables without importing application dependencies."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = ROOT / ".env"

REQUIRED = (
    "APP_SECRET_KEY",
    "BOOTSTRAP_USERNAME",
    "BOOTSTRAP_PASSWORD",
    "BOOTSTRAP_ROLES",
    "INTERNAL_SERVICE_KEY",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "CONTROL_DATABASE",
    "BUSINESS_DATABASE",
    "AGENT_READER_USER",
    "AGENT_READER_PASSWORD",
    "DATABASE_URL",
    "CHECKPOINT_DATABASE_URL",
    "AGENT_DATABASE_URL",
    "REDIS_URL",
    "CORS_ORIGINS",
)

MIN_LENGTHS = {
    "APP_SECRET_KEY": 32,
    "BOOTSTRAP_PASSWORD": 12,
    "INTERNAL_SERVICE_KEY": 24,
    "POSTGRES_PASSWORD": 12,
    "AGENT_READER_PASSWORD": 12,
}

FORBIDDEN_FRAGMENTS = ("change-me", "changeme", "replace-", "<required")

URL_SCHEMES = {
    "DATABASE_URL": {"postgresql+psycopg"},
    "CHECKPOINT_DATABASE_URL": {"postgresql"},
    "AGENT_DATABASE_URL": {"postgresql"},
    "REDIS_URL": {"redis", "rediss"},
}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def validate(values: dict[str, str]) -> list[str]:
    errors: list[str] = []

    for key in REQUIRED:
        if not values.get(key, "").strip():
            errors.append(f"{key} is missing or blank")

    for key, minimum in MIN_LENGTHS.items():
        value = values.get(key, "").strip()
        if value and len(value) < minimum:
            errors.append(f"{key} must contain at least {minimum} characters")
        lowered = value.lower()
        if value and any(fragment in lowered for fragment in FORBIDDEN_FRAGMENTS):
            errors.append(f"{key} still contains an example/placeholder value")

    for key, schemes in URL_SCHEMES.items():
        value = values.get(key, "").strip()
        if not value:
            continue
        parsed = urlsplit(value)
        if parsed.scheme not in schemes:
            errors.append(
                f"{key} must use one of these schemes: {', '.join(sorted(schemes))}"
            )
        if not parsed.hostname:
            errors.append(f"{key} does not contain a valid host")

    if values.get("BUSINESS_DATA_MODE", "embedded").lower() == "external":
        agent_url = values.get("AGENT_DATABASE_URL", "")
        if "@postgres:" in agent_url:
            errors.append(
                "BUSINESS_DATA_MODE=external but AGENT_DATABASE_URL still targets the embedded "
                "Docker PostgreSQL service"
            )

    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args()

    env_file = args.env_file.resolve()
    if not env_file.exists():
        raise SystemExit(
            f"{env_file} does not exist. Run: python scripts/generate_local_env.py"
        )

    errors = validate(parse_env(env_file))
    if errors:
        print(f"Invalid environment configuration: {env_file}")
        for error in errors:
            print(f"- {error}")
        print("Repair blank local values with: python scripts/generate_local_env.py")
        raise SystemExit(1)

    print(f"Environment configuration is valid: {env_file}")


if __name__ == "__main__":
    main()
