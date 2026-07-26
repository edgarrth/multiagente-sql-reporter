from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text

from axiz.pe.sql_agent.core.database import Database
from axiz.pe.sql_agent.models.contracts import ConversationMemory


class ConversationMemoryRepository:
    """Persists one versioned structured-memory document per chat session."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, session_id: UUID, user_id: UUID) -> ConversationMemory:
        statement = text(
            """
            SELECT m.memory, m.revision, m.updated_at
            FROM app.chat_sessions s
            LEFT JOIN app.session_memory m ON m.session_id = s.id
            WHERE s.id = :session_id AND s.user_id = :user_id
            """
        )
        async with self.db.session() as session:
            row = (
                await session.execute(
                    statement,
                    {"session_id": session_id, "user_id": user_id},
                )
            ).mappings().first()
        if row is None:
            raise PermissionError("Session not found or not owned by user")
        if row.get("memory") is None:
            return ConversationMemory()
        payload = dict(row["memory"] or {})
        payload["revision"] = int(row.get("revision") or payload.get("revision") or 0)
        payload["updated_at"] = row.get("updated_at")
        return ConversationMemory.model_validate(payload)

    async def upsert(
        self,
        session_id: UUID,
        user_id: UUID,
        memory: ConversationMemory,
        last_run_id: UUID | None,
    ) -> ConversationMemory:
        statement = text(
            """
            INSERT INTO app.session_memory (session_id, memory, revision, last_run_id)
            SELECT s.id, CAST(:memory AS jsonb), 1, :last_run_id
            FROM app.chat_sessions s
            WHERE s.id = :session_id AND s.user_id = :user_id
            ON CONFLICT (session_id)
            DO UPDATE SET memory = EXCLUDED.memory,
                          revision = app.session_memory.revision + 1,
                          last_run_id = EXCLUDED.last_run_id,
                          updated_at = now()
            RETURNING memory, revision, updated_at
            """
        )
        payload = memory.model_dump(mode="json", exclude={"revision", "updated_at"})
        async with self.db.session() as session:
            row = (
                await session.execute(
                    statement,
                    {
                        "session_id": session_id,
                        "user_id": user_id,
                        "last_run_id": last_run_id,
                        "memory": json.dumps(payload, ensure_ascii=False, default=str),
                    },
                )
            ).mappings().first()
        if row is None:
            raise PermissionError("Session not found or not owned by user")
        stored = dict(row["memory"] or {})
        stored["revision"] = int(row["revision"])
        stored["updated_at"] = row["updated_at"]
        return ConversationMemory.model_validate(stored)
