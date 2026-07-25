from __future__ import annotations

from uuid import UUID

from sqlalchemy import text

from axiz.pe.sql_agent.core.database import Database


class UserRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def find_by_username(self, username: str) -> dict | None:
        statement = text(
            """
            SELECT id, username, password_hash, roles, auth_source, is_active
            FROM app.users
            WHERE lower(username) = lower(:username)
            """
        )
        async with self.db.session() as session:
            row = (await session.execute(statement, {"username": username})).mappings().first()
            return dict(row) if row else None

    async def find_by_external_id(self, external_id: str, auth_source: str) -> dict | None:
        statement = text(
            """
            SELECT id, username, roles, auth_source, is_active
            FROM app.users
            WHERE external_id = :external_id AND auth_source = :auth_source
            """
        )
        async with self.db.session() as session:
            row = (
                await session.execute(
                    statement,
                    {"external_id": external_id, "auth_source": auth_source},
                )
            ).mappings().first()
            return dict(row) if row else None

    async def create_local_user(
        self,
        username: str,
        password_hash: str,
        roles: list[str],
    ) -> UUID:
        statement = text(
            """
            INSERT INTO app.users (username, password_hash, roles, auth_source)
            VALUES (:username, :password_hash, :roles, 'local')
            ON CONFLICT (username) DO UPDATE SET username = EXCLUDED.username
            RETURNING id
            """
        )
        async with self.db.session() as session:
            return (
                await session.execute(
                    statement,
                    {"username": username, "password_hash": password_hash, "roles": roles},
                )
            ).scalar_one()

    async def get_or_create_external_user(
        self,
        external_id: str,
        username: str,
        auth_source: str,
        roles: list[str] | None = None,
    ) -> UUID:
        existing = await self.find_by_external_id(external_id, auth_source)
        if existing:
            return UUID(str(existing["id"]))
        statement = text(
            """
            INSERT INTO app.users (username, external_id, roles, auth_source)
            VALUES (:username, :external_id, :roles, :auth_source)
            RETURNING id
            """
        )
        async with self.db.session() as session:
            return (
                await session.execute(
                    statement,
                    {
                        "username": username,
                        "external_id": external_id,
                        "roles": roles or ["analyst"],
                        "auth_source": auth_source,
                    },
                )
            ).scalar_one()
