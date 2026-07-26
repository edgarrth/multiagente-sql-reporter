from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text

from axiz.pe.sql_agent.core.database import Database


class RunConflictError(RuntimeError):
    def __init__(self, message: str, *, run_id: UUID | None = None, status: str | None = None):
        super().__init__(message)
        self.run_id = run_id
        self.status = status


class RunLeaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunCreateResult:
    run_id: UUID
    created: bool
    row: dict


@dataclass(frozen=True)
class RunResumeClaim:
    row: dict
    replay: bool = False


class RunRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        session_id: UUID,
        user_id: UUID,
        question: str,
        *,
        idempotency_key: str | None = None,
        lease_owner: str | None = None,
        lease_seconds: int = 360,
        max_concurrent_runs_per_user: int = 2,
    ) -> UUID:
        result = await self.create_or_get(
            session_id,
            user_id,
            question,
            idempotency_key=idempotency_key,
            lease_owner=lease_owner or str(uuid4()),
            lease_seconds=lease_seconds,
            max_concurrent_runs_per_user=max_concurrent_runs_per_user,
        )
        return result.run_id

    async def create_or_get(
        self,
        session_id: UUID,
        user_id: UUID,
        question: str,
        *,
        idempotency_key: str | None,
        lease_owner: str,
        lease_seconds: int,
        max_concurrent_runs_per_user: int,
    ) -> RunCreateResult:
        async with self.db.session() as session:
            owner = (
                await session.execute(
                    text(
                        "SELECT id FROM app.chat_sessions "
                        "WHERE id=:session_id AND user_id=:user_id FOR UPDATE"
                    ),
                    {"session_id": session_id, "user_id": user_id},
                )
            ).first()
            if not owner:
                raise PermissionError("Session not found or not owned by user")
            # Serialize concurrency decisions for all sessions of the same user.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:user_id_text))"),
                {"user_id_text": str(user_id)},
            )

            if idempotency_key:
                existing = (
                    await session.execute(
                        text(
                            """
                            SELECT id, session_id, user_id, question, status, state, error,
                                   version, idempotency_key, lease_owner, lease_expires_at,
                                   cancel_requested_at, created_at, updated_at, completed_at
                            FROM app.agent_runs
                            WHERE user_id=:user_id AND idempotency_key=:idempotency_key
                            """
                        ),
                        {"user_id": user_id, "idempotency_key": idempotency_key},
                    )
                ).mappings().first()
                if existing:
                    row = dict(existing)
                    if UUID(str(row["session_id"])) != session_id or str(row["question"]) != question:
                        raise RunConflictError(
                            "Idempotency key was already used with a different run payload.",
                            run_id=UUID(str(row["id"])),
                            status=str(row["status"]),
                        )
                    return RunCreateResult(UUID(str(row["id"])), False, row)

            await session.execute(
                text(
                    """
                    UPDATE app.agent_runs
                    SET status='failed',
                        error='Recovered stale run lease before starting a new run',
                        completed_at=COALESCE(completed_at, now()),
                        updated_at=now(),
                        lease_owner=NULL,
                        lease_expires_at=NULL,
                        heartbeat_at=NULL,
                        version=version+1
                    WHERE status='running'
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at < now()
                    """
                )
            )

            active_count = (
                await session.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM app.agent_runs
                        WHERE user_id=:user_id
                          AND status='running'
                          AND (lease_expires_at IS NULL OR lease_expires_at >= now())
                        """
                    ),
                    {"user_id": user_id},
                )
            ).scalar_one()
            if int(active_count) >= max_concurrent_runs_per_user:
                raise RunConflictError(
                    "The user already has the maximum number of concurrent runs."
                )

            active = (
                await session.execute(
                    text(
                        """
                        SELECT id, status FROM app.agent_runs
                        WHERE session_id=:session_id
                          AND status='running'
                          AND (lease_expires_at IS NULL OR lease_expires_at >= now())
                        ORDER BY created_at DESC LIMIT 1
                        """
                    ),
                    {"session_id": session_id},
                )
            ).mappings().first()
            if active:
                raise RunConflictError(
                    "Another run is already executing in this chat session.",
                    run_id=UUID(str(active["id"])),
                    status=str(active["status"]),
                )

            run_id = uuid4()
            row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO app.agent_runs (
                            id, session_id, user_id, question, status, idempotency_key,
                            lease_owner, lease_expires_at, heartbeat_at, started_at
                        )
                        VALUES (
                            :id, :session_id, :user_id, :question, 'running', :idempotency_key,
                            :lease_owner, now() + (:lease_seconds * interval '1 second'),
                            now(), now()
                        )
                        RETURNING id, session_id, user_id, question, status, state, error,
                                  version, idempotency_key, lease_owner, lease_expires_at,
                                  cancel_requested_at, created_at, updated_at, completed_at
                        """
                    ),
                    {
                        "id": run_id,
                        "session_id": session_id,
                        "user_id": user_id,
                        "question": question,
                        "idempotency_key": idempotency_key,
                        "lease_owner": lease_owner,
                        "lease_seconds": lease_seconds,
                    },
                )
            ).mappings().one()
            return RunCreateResult(run_id, True, dict(row))

    async def claim_resume(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        decision: str,
        comment: str | None,
        idempotency_key: str | None,
        lease_owner: str,
        lease_seconds: int,
        max_concurrent_runs_per_user: int,
    ) -> RunResumeClaim:
        async with self.db.session() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:user_id_text))"),
                {"user_id_text": str(user_id)},
            )
            if idempotency_key:
                existing_feedback = (
                    await session.execute(
                        text(
                            """
                            SELECT decision, comment FROM app.human_feedback
                            WHERE run_id=:run_id AND idempotency_key=:idempotency_key
                            """
                        ),
                        {"run_id": run_id, "idempotency_key": idempotency_key},
                    )
                ).mappings().first()
                if existing_feedback:
                    if (
                        str(existing_feedback["decision"]) != decision
                        or (existing_feedback["comment"] or None) != (comment or None)
                    ):
                        raise RunConflictError(
                            "Idempotency key was already used with different feedback.",
                            run_id=run_id,
                        )
                    row = await self._get_with_session(session, run_id, user_id)
                    if not row:
                        raise PermissionError("Run not found or not owned by user")
                    return RunResumeClaim(row=row, replay=True)

            active_count = (
                await session.execute(
                    text(
                        """
                        SELECT count(*) FROM app.agent_runs
                        WHERE user_id=:user_id AND id<>:run_id
                          AND status='running'
                          AND (lease_expires_at IS NULL OR lease_expires_at >= now())
                        """
                    ),
                    {"user_id": user_id, "run_id": run_id},
                )
            ).scalar_one()
            if int(active_count) >= max_concurrent_runs_per_user:
                raise RunConflictError(
                    "The user already has the maximum number of concurrent runs."
                )

            claimed = (
                await session.execute(
                    text(
                        """
                        UPDATE app.agent_runs
                        SET status='running',
                            lease_owner=:lease_owner,
                            lease_expires_at=now() + (:lease_seconds * interval '1 second'),
                            heartbeat_at=now(),
                            cancel_requested_at=NULL,
                            updated_at=now(),
                            version=version+1
                        WHERE id=:run_id AND user_id=:user_id
                          AND status='awaiting_approval'
                          AND (lease_expires_at IS NULL OR lease_expires_at < now())
                        RETURNING id, session_id, user_id, question, status, state, error,
                                  version, idempotency_key, lease_owner, lease_expires_at,
                                  cancel_requested_at, created_at, updated_at, completed_at
                        """
                    ),
                    {
                        "run_id": run_id,
                        "user_id": user_id,
                        "lease_owner": lease_owner,
                        "lease_seconds": lease_seconds,
                    },
                )
            ).mappings().first()
            if not claimed:
                row = await self._get_with_session(session, run_id, user_id)
                if not row:
                    raise PermissionError("Run not found or not owned by user")
                raise RunConflictError(
                    f"Run is not available for feedback; current status is {row['status']}",
                    run_id=run_id,
                    status=str(row["status"]),
                )

            await session.execute(
                text(
                    """
                    INSERT INTO app.human_feedback (
                        run_id, user_id, decision, comment, idempotency_key, run_version
                    )
                    VALUES (
                        :run_id, :user_id, :decision, :comment, :idempotency_key, :run_version
                    )
                    ON CONFLICT (run_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL DO NOTHING
                    """
                ),
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "decision": decision,
                    "comment": comment,
                    "idempotency_key": idempotency_key,
                    "run_version": int(claimed["version"]),
                },
            )
            return RunResumeClaim(row=dict(claimed), replay=False)

    async def heartbeat(self, run_id: UUID, lease_owner: str, lease_seconds: int) -> bool:
        async with self.db.session() as session:
            result = await session.execute(
                text(
                    """
                    UPDATE app.agent_runs
                    SET heartbeat_at=now(),
                        lease_expires_at=now() + (:lease_seconds * interval '1 second'),
                        updated_at=now()
                    WHERE id=:run_id AND status='running' AND lease_owner=:lease_owner
                    """
                ),
                {
                    "run_id": run_id,
                    "lease_owner": lease_owner,
                    "lease_seconds": lease_seconds,
                },
            )
            return result.rowcount == 1

    async def is_cancel_requested(self, run_id: UUID, lease_owner: str) -> bool:
        statement = text(
            """
            SELECT cancel_requested_at IS NOT NULL AS requested
            FROM app.agent_runs
            WHERE id=:run_id AND lease_owner=:lease_owner
            """
        )
        async with self.db.session() as session:
            value = (await session.execute(statement, {"run_id": run_id, "lease_owner": lease_owner})).scalar()
            return bool(value)

    async def request_cancel(self, run_id: UUID, user_id: UUID) -> dict | None:
        statement = text(
            """
            UPDATE app.agent_runs
            SET cancel_requested_at=now(),
                status=CASE WHEN status='awaiting_approval' THEN 'cancelled' ELSE status END,
                completed_at=CASE
                    WHEN status='awaiting_approval' THEN COALESCE(completed_at, now())
                    ELSE completed_at
                END,
                lease_owner=CASE WHEN status='awaiting_approval' THEN NULL ELSE lease_owner END,
                lease_expires_at=CASE
                    WHEN status='awaiting_approval' THEN NULL ELSE lease_expires_at
                END,
                heartbeat_at=CASE WHEN status='awaiting_approval' THEN NULL ELSE heartbeat_at END,
                updated_at=now(),
                version=version+1
            WHERE id=:run_id AND user_id=:user_id
              AND status IN ('running', 'awaiting_approval')
            RETURNING id, status, version
            """
        )
        async with self.db.session() as session:
            row = (await session.execute(statement, {"run_id": run_id, "user_id": user_id})).mappings().first()
            return dict(row) if row else None

    async def recover_stale_runs(self) -> int:
        statement = text(
            """
            UPDATE app.agent_runs
            SET status='failed',
                error='Recovered stale run after expired execution lease',
                completed_at=COALESCE(completed_at, now()),
                updated_at=now(),
                lease_owner=NULL,
                lease_expires_at=NULL,
                heartbeat_at=NULL,
                version=version+1
            WHERE status='running'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < now()
            """
        )
        async with self.db.session() as session:
            result = await session.execute(statement)
            return int(result.rowcount or 0)

    async def update(
        self,
        run_id: UUID,
        status: str,
        state: dict | None = None,
        error: str | None = None,
        *,
        lease_owner: str | None = None,
    ) -> int:
        terminal_statuses = {"completed", "failed", "rejected", "needs_clarification", "cancelled"}
        releases_lease = status != "running"
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
                    WHEN CAST(:is_terminal AS boolean) THEN COALESCE(completed_at, now())
                    ELSE completed_at
                END,
                lease_owner = CASE WHEN CAST(:releases_lease AS boolean) THEN NULL ELSE lease_owner END,
                lease_expires_at = CASE WHEN CAST(:releases_lease AS boolean) THEN NULL ELSE lease_expires_at END,
                heartbeat_at = CASE WHEN CAST(:releases_lease AS boolean) THEN NULL ELSE heartbeat_at END,
                version = version + 1
            WHERE id = :run_id
              AND (:lease_owner IS NULL OR lease_owner=:lease_owner)
            RETURNING version
            """
        )
        async with self.db.session() as session:
            result = await session.execute(
                statement,
                {
                    "run_id": run_id,
                    "status": status,
                    "has_state": state is not None,
                    "state_json": json.dumps(state, default=str) if state is not None else None,
                    "error": error,
                    "is_terminal": status in terminal_statuses,
                    "releases_lease": releases_lease,
                    "lease_owner": lease_owner,
                },
            )
            # Some unit-test fakes intentionally return None and only capture SQL.
            if result is None:
                return 0
            version = result.scalar()
            if version is None:
                raise RunLeaseError(
                    f"Run {run_id} is no longer owned by execution lease {lease_owner!r}"
                )
            return int(version)

    async def get(self, run_id: UUID, user_id: UUID | None = None) -> dict | None:
        async with self.db.session() as session:
            return await self._get_with_session(session, run_id, user_id)

    async def _get_with_session(self, session, run_id: UUID, user_id: UUID | None) -> dict | None:
        where = "id = :run_id"
        params: dict = {"run_id": run_id}
        if user_id is not None:
            where += " AND user_id = :user_id"
            params["user_id"] = user_id
        statement = text(
            f"""
            SELECT id, session_id, user_id, question, status, state, error,
                   version, idempotency_key, lease_owner, lease_expires_at,
                   heartbeat_at, cancel_requested_at, started_at,
                   created_at, updated_at, completed_at
            FROM app.agent_runs WHERE {where}
            """
        )
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
