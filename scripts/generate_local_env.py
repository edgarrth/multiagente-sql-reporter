#!/usr/bin/env python3
"""Create or repair a local ``.env`` without publishing reusable credentials.

The command is intentionally idempotent:

* when ``.env`` does not exist, it is generated from ``.env.example``;
* when it already exists, non-empty values are preserved and only missing/blank
  secrets and derived connection URLs are completed;
* ``--force`` recreates the file and rotates generated secrets;
* ``--refresh-urls`` rebuilds connection URLs from the current primitive values.
"""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".env.example"
DEFAULT_OUTPUT = ROOT / ".env"

SECRET_SPECS: dict[str, int] = {
    "APP_SECRET_KEY": 48,
    "BOOTSTRAP_PASSWORD": 18,
    "INTERNAL_SERVICE_KEY": 40,
    "POSTGRES_PASSWORD": 24,
    "AGENT_READER_PASSWORD": 24,
}

DERIVED_URL_KEYS = (
    "DATABASE_URL",
    "CHECKPOINT_DATABASE_URL",
    "AGENT_DATABASE_URL",
    "REDIS_URL",
)


def parse_env(text: str) -> dict[str, str]:
    """Parse simple KEY=VALUE entries while preserving insertion order."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value
    return values


def render_env(
    template_text: str,
    values: dict[str, str],
    custom_values: dict[str, str],
) -> str:
    rendered: list[str] = []
    for raw_line in template_text.splitlines():
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and "=" in raw_line:
            key = raw_line.split("=", 1)[0].strip()
            rendered.append(f"{key}={values.get(key, '')}")
        else:
            rendered.append(raw_line)

    if custom_values:
        rendered.extend(
            [
                "",
                "# -----------------------------------------------------------------------------",
                "# Custom variables preserved from the previous .env",
                "# -----------------------------------------------------------------------------",
            ]
        )
        rendered.extend(f"{key}={value}" for key, value in custom_values.items())

    return "\n".join(rendered).rstrip() + "\n"


def secret(length: int) -> str:
    return secrets.token_urlsafe(length)


def required_value(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise SystemExit(
            f"Cannot derive connection URLs because {key} is blank. "
            "Set it in .env.example or the existing .env and run the command again."
        )
    return value


def build_connection_urls(values: dict[str, str]) -> dict[str, str]:
    postgres_user = quote(required_value(values, "POSTGRES_USER"), safe="")
    postgres_password = quote(required_value(values, "POSTGRES_PASSWORD"), safe="")
    reader_user = quote(required_value(values, "AGENT_READER_USER"), safe="")
    reader_password = quote(required_value(values, "AGENT_READER_PASSWORD"), safe="")
    postgres_host = required_value(values, "POSTGRES_HOST")
    postgres_port = required_value(values, "POSTGRES_PORT")
    control_db = required_value(values, "CONTROL_DATABASE")
    business_db = required_value(values, "BUSINESS_DATABASE")
    redis_host = required_value(values, "REDIS_HOST")
    redis_port = required_value(values, "REDIS_PORT")
    redis_database = required_value(values, "REDIS_DATABASE")

    return {
        "DATABASE_URL": (
            f"postgresql+psycopg://{postgres_user}:{postgres_password}"
            f"@{postgres_host}:{postgres_port}/{control_db}"
        ),
        "CHECKPOINT_DATABASE_URL": (
            f"postgresql://{postgres_user}:{postgres_password}"
            f"@{postgres_host}:{postgres_port}/{control_db}"
        ),
        "AGENT_DATABASE_URL": (
            f"postgresql://{reader_user}:{reader_password}"
            f"@{postgres_host}:{postgres_port}/{business_db}"
        ),
        "REDIS_URL": f"redis://{redis_host}:{redis_port}/{redis_database}",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recreate the file and rotate generated local secrets.",
    )
    parser.add_argument(
        "--refresh-urls",
        action="store_true",
        help="Rebuild connection URLs from the current host/user/password variables.",
    )
    args = parser.parse_args()

    output = args.output.resolve()
    template_text = TEMPLATE.read_text(encoding="utf-8")
    template_values = parse_env(template_text)

    existing_text = ""
    existing_values: dict[str, str] = {}
    if output.exists() and not args.force:
        existing_text = output.read_text(encoding="utf-8")
        existing_values = parse_env(existing_text)

    values = dict(template_values)
    if existing_values:
        values.update(existing_values)

    generated_keys: list[str] = []
    for key, length in SECRET_SPECS.items():
        if args.force or not values.get(key, "").strip():
            values[key] = secret(length)
            generated_keys.append(key)

    derived = build_connection_urls(values)
    business_data_mode = values.get("BUSINESS_DATA_MODE", "embedded").strip().lower()
    refreshed_url_keys: list[str] = []
    for key in DERIVED_URL_KEYS:
        should_refresh = args.force or args.refresh_urls or not values.get(key, "").strip()
        if key == "AGENT_DATABASE_URL" and business_data_mode == "external":
            # An external read-only URL cannot be inferred safely from the embedded DB variables.
            should_refresh = args.force and not existing_values
        if should_refresh:
            values[key] = derived[key]
            refreshed_url_keys.append(key)

    custom_values = {
        key: value for key, value in existing_values.items() if key not in template_values
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_env(template_text, values, custom_values), encoding="utf-8")
    try:
        output.chmod(0o600)
    except OSError:
        # Some mounted or non-POSIX filesystems do not support chmod semantics.
        pass

    action = "Recreated" if args.force else ("Repaired" if existing_text else "Generated")
    print(f"{action} {output}")
    if generated_keys:
        print("Generated missing local secrets: " + ", ".join(generated_keys))
    if refreshed_url_keys:
        print("Completed connection URLs: " + ", ".join(refreshed_url_keys))
    if business_data_mode == "external" and not values.get("AGENT_DATABASE_URL", "").strip():
        print("AGENT_DATABASE_URL is still required because BUSINESS_DATA_MODE=external.")
    if args.force:
        print("--force rotated generated secrets and recreated the file from the template.")
    else:
        print("Existing non-empty values, including provider API keys, were preserved.")
    print("Run: python scripts/validate_env.py")


if __name__ == "__main__":
    main()
