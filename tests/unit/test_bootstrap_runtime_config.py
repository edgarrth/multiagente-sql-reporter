from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.services.auth_service import AuthService


@dataclass
class FakeUsers:
    existing: dict[str, Any] | None = None
    writes: list[dict[str, Any]] = field(default_factory=list)

    async def find_by_username(self, username: str) -> dict[str, Any] | None:
        return self.existing

    async def create_local_user(
        self,
        username: str,
        password_hash: str,
        roles: list[str],
    ) -> None:
        self.writes.append(
            {"username": username, "password_hash": password_hash, "roles": roles}
        )


class FakePasswords:
    def hash(self, password: str) -> str:
        return f"hashed::{password}"


class FakeTokens:
    pass


def settings(*, sync: bool) -> Settings:
    return Settings(
        app_secret_key="a" * 40,
        bootstrap_username="runtime-admin",
        bootstrap_password="RuntimePassword123!",
        bootstrap_roles=["platform-admin", "analyst"],
        bootstrap_sync_credentials=sync,
        internal_service_key="i" * 32,
        database_url="postgresql+psycopg://owner:pwd@db/control",
        checkpoint_database_url="postgresql://owner:pwd@db/control",
        agent_database_url="postgresql://reader:pwd@db/business",
        redis_url="redis://redis:6379/0",
        cors_origins=["http://localhost:8501"],
    )


@pytest.mark.asyncio
async def test_bootstrap_creates_user_from_runtime_configuration() -> None:
    users = FakeUsers()
    service = AuthService(settings(sync=False), users, FakePasswords(), FakeTokens())

    await service.bootstrap()

    assert users.writes == [
        {
            "username": "runtime-admin",
            "password_hash": "hashed::RuntimePassword123!",
            "roles": ["platform-admin", "analyst"],
        }
    ]


@pytest.mark.asyncio
async def test_bootstrap_can_sync_existing_local_credentials() -> None:
    users = FakeUsers(existing={"auth_source": "local"})
    service = AuthService(settings(sync=True), users, FakePasswords(), FakeTokens())

    await service.bootstrap()

    assert len(users.writes) == 1


@pytest.mark.asyncio
async def test_bootstrap_does_not_overwrite_external_identity() -> None:
    users = FakeUsers(existing={"auth_source": "entra-id"})
    service = AuthService(settings(sync=True), users, FakePasswords(), FakeTokens())

    with pytest.raises(RuntimeError, match="non-local"):
        await service.bootstrap()

    assert users.writes == []
