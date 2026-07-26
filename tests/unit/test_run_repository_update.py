from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from axiz.pe.sql_agent.repositories.run_repository import RunRepository


class CapturingSession:
    def __init__(self) -> None:
        self.statement = None
        self.params = None

    async def execute(self, statement, params):
        self.statement = statement
        self.params = params


class FakeDatabase:
    def __init__(self) -> None:
        self.captured = CapturingSession()

    @asynccontextmanager
    async def session(self):
        yield self.captured


@pytest.mark.asyncio
async def test_update_uses_explicit_boolean_flags_and_no_reused_status_parameter() -> None:
    db = FakeDatabase()
    repo = RunRepository(db)  # type: ignore[arg-type]
    await repo.update(uuid4(), "failed", state={"error": "boom"}, error="boom")

    sql = str(db.captured.statement)
    assert sql.count(":status") == 1
    assert ":is_terminal" in sql
    assert ":has_state" in sql
    assert db.captured.params["is_terminal"] is True
    assert db.captured.params["has_state"] is True


@pytest.mark.asyncio
async def test_update_with_lease_uses_typed_ownership_predicate() -> None:
    db = FakeDatabase()
    repo = RunRepository(db)  # type: ignore[arg-type]
    owner = "worker-123"

    await repo.update(uuid4(), "awaiting_approval", state={"ok": True}, lease_owner=owner)

    sql = str(db.captured.statement)
    assert ":lease_owner IS NULL" not in sql
    assert "lease_owner = CAST(:lease_owner AS varchar)" in sql
    assert db.captured.params["lease_owner"] == owner


@pytest.mark.asyncio
async def test_update_without_lease_omits_ownership_predicate_and_parameter() -> None:
    db = FakeDatabase()
    repo = RunRepository(db)  # type: ignore[arg-type]

    await repo.update(uuid4(), "failed", state={"error": "boom"}, error="boom")

    sql = str(db.captured.statement)
    assert ":lease_owner" not in sql
    assert "lease_owner = CAST" not in sql
    assert "lease_owner" not in db.captured.params
