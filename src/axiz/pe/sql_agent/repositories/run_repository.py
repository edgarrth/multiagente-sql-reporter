from __future__ import annotations

import json
from uuid import UUID, uuid4

from sqlalchemy import text

from axiz.pe.sql_agent.core.database import Database


class RunRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, session_id: UUID, user_id: UUID, question: str) -> UUID:
        run_id = uuid4()
        statement = text(
            """
            INSERT INTO app.agent_runs (id, session_id, user_id, question, status)
            VALUES (:id, :session_id, :user_id, :question, 'running')
            """
        )
        async with self.db.session() as session:
            await session.execute(
                statement,
                {
                    "id": run_id,
                    "session_id": session_id,
                    "user_id": user_id,
                    "question": question,
                },
            )
        return run_id

    async def update(
        self,
        run_id: UUID,
        status: str,
        state: dict | None = None,
        error: str | None = None,
    ) -> None:
        terminal_statuses = {"completed", "failed", "rejected", "needs_clarification"}
        statement = text(
            """
            UPDATE app.agent_runs
            SET status = CAST(:status AS varchar),
                state = CASE
                    WHEN CAST(:has_state AS boolean) THEN CAST(:state_json AS jsonb)
                    ELSE state
                END,
                error = CAST(:error AS text),
                updated_at = now(),
                completed_at = CASE
                    WHEN CAST(:is_terminal AS boolean) THEN now()
                    ELSE completed_at
                END
            WHERE id = :run_id
            """
        )
        async with self.db.session() as session:
            await session.execute(
                statement,
                {
                    "run_id": run_id,
                    "status": status,
                    "has_state": state is not None,
                    "state_json": json.dumps(state, default=str) if state is not None else None,
                    "error": error,
                    "is_terminal": status in terminal_statuses,
                },
            )

    async def get(self, run_id: UUID, user_id: UUID | None = None) -> dict | None:
        where = "id = :run_id"
        params: dict = {"run_id": run_id}
        if user_id is not None:
            where += " AND user_id = :user_id"
            params["user_id"] = user_id
        statement = text(
            f"""
            SELECT id, session_id, user_id, question, status, state, error,
                   created_at, updated_at, completed_at
            FROM app.agent_runs WHERE {where}
            """
        )
        async with self.db.session() as session:
            row = (await session.execute(statement, params)).mappings().first()
            return dict(row) if row else None

    async def add_feedback(
        self,
        run_id: UUID,
        user_id: UUID,
        decision: str,
        comment: str | None,
    ) -> None:
        statement = text(
            """
            INSERT INTO app.human_feedback (run_id, user_id, decision, comment)
            VALUES (:run_id, :user_id, :decision, :comment)
            """
        )
        async with self.db.session() as session:
            await session.execute(
                statement,
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "decision": decision,
                    "comment": comment,
                },
            )

    async def audit(
        self,
        run_id: UUID | None,
        user_id: UUID | None,
        event_type: str,
        payload: dict,
    ) -> None:
        statement = text(
            """
            INSERT INTO app.audit_events (run_id, user_id, event_type, payload)
            VALUES (:run_id, :user_id, :event_type, CAST(:payload AS jsonb))
            """
        )
        async with self.db.session() as session:
            await session.execute(
                statement,
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "event_type": event_type,
                    "payload": json.dumps(payload, default=str),
                },
            )
