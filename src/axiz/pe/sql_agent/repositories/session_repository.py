from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from axiz.pe.sql_agent.core.database import Database


_ZERO_TOKEN_USAGE_SQL = """
jsonb_build_object(
    'runs', 0,
    'llm_calls', 0,
    'input_tokens', 0,
    'output_tokens', 0,
    'total_tokens', 0,
    'cached_input_tokens', 0,
    'reasoning_output_tokens', 0
)
""".strip()

_AGGREGATED_TOKEN_USAGE_SQL = """
jsonb_build_object(
    'runs', count(*),
    'llm_calls', COALESCE(sum(COALESCE(NULLIF(state->'llm_usage'->>'call_count', '')::bigint, 0)), 0),
    'input_tokens', COALESCE(sum(COALESCE(NULLIF(state->'llm_usage'->>'actual_input_tokens', '')::bigint, 0)), 0),
    'output_tokens', COALESCE(sum(COALESCE(NULLIF(state->'llm_usage'->>'actual_output_tokens', '')::bigint, 0)), 0),
    'total_tokens', COALESCE(sum(COALESCE(NULLIF(state->'llm_usage'->>'actual_total_tokens', '')::bigint, 0)), 0),
    'cached_input_tokens', COALESCE(sum(COALESCE(
        NULLIF(state->'llm_usage'->>'cached_input_tokens', '')::bigint, 0
    )), 0),
    'reasoning_output_tokens', COALESCE(sum(COALESCE(
        NULLIF(state->'llm_usage'->>'reasoning_output_tokens', '')::bigint, 0
    )), 0)
)
""".strip()


class SessionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, user_id: UUID, title: str | None = None) -> dict:
        session_id = uuid4()
        statement = text(
            f"""
            INSERT INTO app.chat_sessions (id, user_id, title)
            VALUES (:id, :user_id, :title)
            RETURNING id, title, created_at, updated_at,
                      NULL::uuid AS pending_run_id,
                      0::bigint AS message_count,
                      {_ZERO_TOKEN_USAGE_SQL} AS token_usage
            """
        )
        async with self.db.session() as session:
            row = (
                await session.execute(
                    statement,
                    {
                        "id": session_id,
                        "user_id": user_id,
                        "title": title or "Nueva conversación",
                    },
                )
            ).mappings().one()
            return dict(row)

    async def list_by_user(self, user_id: UUID, limit: int = 100) -> list[dict]:
        statement = text(
            f"""
            SELECT s.id,
                   s.title,
                   s.created_at,
                   s.updated_at,
                   (
                       SELECT r.id
                       FROM app.agent_runs r
                       WHERE r.session_id = s.id
                         AND r.status = 'awaiting_approval'
                       ORDER BY r.updated_at DESC
                       LIMIT 1
                   ) AS pending_run_id,
                   (
                       SELECT count(*)
                       FROM app.chat_messages m
                       WHERE m.session_id = s.id
                   ) AS message_count,
                   COALESCE(usage.token_usage, {_ZERO_TOKEN_USAGE_SQL}) AS token_usage
            FROM app.chat_sessions s
            LEFT JOIN LATERAL (
                SELECT {_AGGREGATED_TOKEN_USAGE_SQL.replace("state->", "r.state->")} AS token_usage
                FROM app.agent_runs r
                WHERE r.session_id = s.id
            ) usage ON TRUE
            WHERE s.user_id = :user_id
            ORDER BY s.updated_at DESC
            LIMIT :limit
            """
        )
        async with self.db.session() as session:
            rows = (
                await session.execute(statement, {"user_id": user_id, "limit": limit})
            ).mappings().all()
            return [dict(row) for row in rows]

    async def rename(self, session_id: UUID, user_id: UUID, title: str) -> dict:
        statement = text(
            f"""
            WITH updated AS (
                UPDATE app.chat_sessions
                SET title = :title, updated_at = now()
                WHERE id = :session_id AND user_id = :user_id
                RETURNING id, title, created_at, updated_at
            )
            SELECT u.id,
                   u.title,
                   u.created_at,
                   u.updated_at,
                   (
                       SELECT r.id
                       FROM app.agent_runs r
                       WHERE r.session_id = u.id
                         AND r.status = 'awaiting_approval'
                       ORDER BY r.updated_at DESC
                       LIMIT 1
                   ) AS pending_run_id,
                   (
                       SELECT count(*)
                       FROM app.chat_messages m
                       WHERE m.session_id = u.id
                   ) AS message_count,
                   COALESCE(usage.token_usage, {_ZERO_TOKEN_USAGE_SQL}) AS token_usage
            FROM updated u
            LEFT JOIN LATERAL (
                SELECT {_AGGREGATED_TOKEN_USAGE_SQL.replace("state->", "r.state->")} AS token_usage
                FROM app.agent_runs r
                WHERE r.session_id = u.id
            ) usage ON TRUE
            """
        )
        async with self.db.session() as session:
            row = (
                await session.execute(
                    statement,
                    {
                        "session_id": session_id,
                        "user_id": user_id,
                        "title": title.strip(),
                    },
                )
            ).mappings().first()
            if not row:
                raise PermissionError("Session not found or not owned by user")
            return dict(row)

    async def get_usage(self, session_id: UUID, user_id: UUID) -> dict:
        await self.assert_owner(session_id, user_id)
        statement = text(
            f"""
            SELECT {_AGGREGATED_TOKEN_USAGE_SQL} AS token_usage
            FROM app.agent_runs
            WHERE session_id = :session_id
            """
        )
        async with self.db.session() as session:
            row = (
                await session.execute(statement, {"session_id": session_id})
            ).mappings().one()
            return dict(row["token_usage"] or {})

    async def delete(self, session_id: UUID, user_id: UUID) -> dict:
        ownership = text(
            "SELECT 1 FROM app.chat_sessions WHERE id = :session_id AND user_id = :user_id"
        )
        params = {"session_id": session_id, "user_id": user_id}
        async with self.db.session() as session:
            exists = (await session.execute(ownership, params)).scalar_one_or_none()
            if not exists:
                raise PermissionError("Session not found or not owned by user")

            checkpoint_tables = (
                await session.execute(
                    text(
                        """
                        SELECT to_regclass('public.checkpoint_writes') IS NOT NULL AS writes,
                               to_regclass('public.checkpoint_blobs') IS NOT NULL AS blobs,
                               to_regclass('public.checkpoints') IS NOT NULL AS checkpoints
                        """
                    )
                )
            ).mappings().one()

            run_selector = (
                "SELECT id::text FROM app.agent_runs WHERE session_id = :session_id"
            )
            if checkpoint_tables["writes"]:
                await session.execute(
                    text(
                        f"DELETE FROM checkpoint_writes WHERE thread_id IN ({run_selector})"
                    ),
                    params,
                )
            if checkpoint_tables["blobs"]:
                await session.execute(
                    text(
                        f"DELETE FROM checkpoint_blobs WHERE thread_id IN ({run_selector})"
                    ),
                    params,
                )
            if checkpoint_tables["checkpoints"]:
                await session.execute(
                    text(f"DELETE FROM checkpoints WHERE thread_id IN ({run_selector})"),
                    params,
                )

            await session.execute(
                text("DELETE FROM app.channel_sessions WHERE session_id = :session_id"),
                params,
            )
            await session.execute(
                text("DELETE FROM app.agent_runs WHERE session_id = :session_id"),
                params,
            )
            row = (
                await session.execute(
                    text(
                        """
                        DELETE FROM app.chat_sessions
                        WHERE id = :session_id AND user_id = :user_id
                        RETURNING id
                        """
                    ),
                    params,
                )
            ).mappings().first()
            if not row:
                raise PermissionError("Session not found or not owned by user")
            return {"id": row["id"], "deleted": True}

    async def auto_title_from_question(self, session_id: UUID, question: str) -> None:
        cleaned = " ".join(question.strip().split())
        if not cleaned:
            return
        title = cleaned[:72].rstrip(" .,:;-")
        statement = text(
            """
            UPDATE app.chat_sessions
            SET title = :title, updated_at = now()
            WHERE id = :session_id
              AND title IN ('New conversation', 'Streamlit conversation', 'Nueva conversación')
            """
        )
        async with self.db.session() as session:
            await session.execute(
                statement,
                {
                    "session_id": session_id,
                    "title": title,
                },
            )

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
    ) -> dict:
        insert_statement = text(
            """
            INSERT INTO app.chat_messages (session_id, role, content, metadata)
            VALUES (:session_id, :role, :content, CAST(:metadata AS jsonb))
            RETURNING id, session_id, role, content, metadata, created_at
            """
        )
        update_statement = text(
            "UPDATE app.chat_sessions SET updated_at = now() WHERE id = :session_id"
        )

        async with self.db.session() as session:
            params = {
                "session_id": session_id,
                "role": role,
                "content": content,
                "metadata": json.dumps(metadata or {}, default=str),
            }
            row = (await session.execute(insert_statement, params)).mappings().one()
            await session.execute(update_statement, {"session_id": session_id})
            return dict(row)

    async def list_messages(
        self,
        session_id: UUID,
        user_id: UUID,
        limit: int = 500,
    ) -> list[dict]:
        await self.assert_owner(session_id, user_id)
        statement = text(
            """
            SELECT id, session_id, role, content, metadata, created_at
            FROM app.chat_messages
            WHERE session_id = :session_id
            ORDER BY created_at ASC, id ASC
            LIMIT :limit
            """
        )
        async with self.db.session() as session:
            rows = (
                await session.execute(
                    statement,
                    {"session_id": session_id, "limit": limit},
                )
            ).mappings().all()
            return [dict(row) for row in rows]

    async def latest_analytical_payload(self, session_id: UUID) -> dict[str, Any] | None:
        """Return the latest persisted assistant payload containing a usable SQL proposal.

        This supports backward-compatible recovery when older versions overwrote structured
        memory after a failed follow-up. Only structured JSON metadata is read; message text is
        never scraped or interpreted.
        """
        statement = text(
            """
            SELECT metadata -> 'payload' AS payload
            FROM app.chat_messages
            WHERE session_id = :session_id
              AND role = 'assistant'
              AND jsonb_typeof(metadata -> 'payload') = 'object'
              AND NULLIF(BTRIM(metadata -> 'payload' ->> 'sql'), '') IS NOT NULL
              AND COALESCE(metadata -> 'payload' ->> 'status', '')
                    IN ('awaiting_approval', 'completed')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        )
        async with self.db.session() as session:
            row = (
                await session.execute(statement, {"session_id": session_id})
            ).mappings().first()
            if not row or not isinstance(row.get("payload"), dict):
                return None
            return dict(row["payload"])

    async def get_history(self, session_id: UUID, limit: int = 16) -> list[dict[str, str]]:
        statement = text(
            """
            SELECT role, content, metadata
            FROM (
                SELECT id, role, content, metadata, created_at
                FROM app.chat_messages
                WHERE session_id = :session_id
                  AND COALESCE(metadata ->> 'exclude_from_context', 'false') <> 'true'
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
            ) recent
            ORDER BY created_at ASC, id ASC
            """
        )
        async with self.db.session() as session:
            rows = (
                await session.execute(statement, {"session_id": session_id, "limit": limit})
            ).mappings().all()
            return [
                {
                    "role": str(row["role"]),
                    "content": self._context_content(
                        str(row["role"]),
                        str(row["content"]),
                        dict(row.get("metadata") or {}),
                    ),
                }
                for row in rows
            ]

    @staticmethod
    def _context_content(role: str, content: str, metadata: dict[str, Any]) -> str:
        """Build a bounded session-memory representation from persisted message metadata."""
        if role != "assistant":
            return content

        payload = metadata.get("payload")
        if not isinstance(payload, dict):
            return content

        parts = [content]
        if payload.get("interpretation"):
            parts.append(f"Interpretación registrada: {payload['interpretation']}")
        if payload.get("sql"):
            compact_sql = " ".join(str(payload["sql"]).split())
            parts.append(f"SQL ejecutado o propuesto: {compact_sql}")
        if payload.get("answer"):
            parts.append(f"Respuesta registrada: {payload['answer']}")
        if payload.get("key_findings"):
            parts.append(
                "Hallazgos: " + "; ".join(str(item) for item in payload["key_findings"][:8])
            )

        result = payload.get("result") or {}
        if isinstance(result, dict) and result:
            columns = [str(item) for item in result.get("columns") or []]
            rows = list(result.get("rows") or [])[:5]
            parts.append(
                "Resultado SQL: "
                f"{int(result.get('row_count') or len(rows))} filas; "
                f"columnas={columns}; muestra={json.dumps(rows, ensure_ascii=False, default=str)}"
            )

        usage = payload.get("llm_usage") or {}
        if isinstance(usage, dict) and usage.get("call_count"):
            models: list[str] = []
            for call in usage.get("calls") or []:
                model = str(call.get("model") or "").strip()
                if model and model not in models:
                    models.append(model)
            parts.append(
                "Consumo LLM: "
                f"modelos={models}; llamadas={usage.get('call_count')}; "
                f"tokens={usage.get('actual_total_tokens')}"
            )

        return "\n".join(parts)[:12000]

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
