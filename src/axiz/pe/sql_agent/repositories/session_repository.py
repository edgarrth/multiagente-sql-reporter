from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text

from axiz.pe.sql_agent.core.database import Database


class SessionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, user_id: UUID, title: str | None = None) -> dict:
        session_id = uuid4()
        statement = text(
            """
            INSERT INTO app.chat_sessions (id, user_id, title)
            VALUES (:id, :user_id, :title)
            RETURNING id, title, created_at, updated_at
            """
        )
        async with self.db.session() as session:
            row = (
                await session.execute(
                    statement,
                    {"id": session_id, "user_id": user_id, "title": title or "New conversation"},
                )
            ).mappings().one()
            return dict(row)

    async def list_by_user(self, user_id: UUID, limit: int = 50) -> list[dict]:
        statement = text(
            """
            SELECT id, title, created_at, updated_at
            FROM app.chat_sessions
            WHERE user_id = :user_id
            ORDER BY updated_at DESC
            LIMIT :limit
            """
        )
        async with self.db.session() as session:
            rows = (
                await session.execute(statement, {"user_id": user_id, "limit": limit})
            ).mappings().all()
            return [dict(row) for row in rows]

    async def assert_owner(self, session_id: UUID, user_id: UUID) -> None:
        statement = text(
            "SELECT 1 FROM app.chat_sessions WHERE id = :session_id AND user_id = :user_id"
        )
        async with self.db.session() as session:
            exists = (
                await session.execute(
                    statement,
                    {"session_id": session_id, "user_id": user_id},
                )
            ).scalar_one_or_none()
            if not exists:
                raise PermissionError("Session not found or not owned by user")

    async def add_message(
        self,
        session_id: UUID,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        insert_statement = text(
            """
            INSERT INTO app.chat_messages (session_id, role, content, metadata)
            VALUES (:session_id, :role, :content, CAST(:metadata AS jsonb))
            """
        )
        update_statement = text(
            "UPDATE app.chat_sessions SET updated_at = now() WHERE id = :session_id"
        )
        import json

        async with self.db.session() as session:
            params = {
                "session_id": session_id,
                "role": role,
                "content": content,
                "metadata": json.dumps(metadata or {}),
            }
            await session.execute(insert_statement, params)
            await session.execute(update_statement, {"session_id": session_id})

    async def get_history(self, session_id: UUID, limit: int = 12) -> list[dict[str, str]]:
        statement = text(
            """
            SELECT role, content
            FROM (
                SELECT id, role, content, created_at
                FROM app.chat_messages
                WHERE session_id = :session_id
                ORDER BY created_at DESC
                LIMIT :limit
            ) recent
            ORDER BY created_at ASC
            """
        )
        async with self.db.session() as session:
            rows = (
                await session.execute(statement, {"session_id": session_id, "limit": limit})
            ).mappings().all()
            return [{"role": str(row["role"]), "content": str(row["content"])} for row in rows]

    async def get_or_create_channel_session(
        self,
        user_id: UUID,
        channel: str,
        conversation_id: str,
    ) -> UUID:
        select_statement = text(
            """
            SELECT session_id FROM app.channel_sessions
            WHERE channel = :channel AND conversation_id = :conversation_id AND user_id = :user_id
            """
        )
        async with self.db.session() as session:
            existing = (
                await session.execute(
                    select_statement,
                    {
                        "channel": channel,
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                    },
                )
            ).scalar_one_or_none()
            if existing:
                return UUID(str(existing))

        created = await self.create(user_id, title=f"{channel.title()} conversation")
        session_id = UUID(str(created["id"]))
        insert_statement = text(
            """
            INSERT INTO app.channel_sessions (channel, conversation_id, user_id, session_id)
            VALUES (:channel, :conversation_id, :user_id, :session_id)
            ON CONFLICT (channel, conversation_id, user_id)
            DO UPDATE SET session_id = EXCLUDED.session_id
            """
        )
        async with self.db.session() as session:
            await session.execute(
                insert_statement,
                {
                    "channel": channel,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "session_id": session_id,
                },
            )
        return session_id
