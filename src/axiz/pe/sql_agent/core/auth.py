from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.models.contracts import UserPrincipal


class PasswordService:
    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except VerifyMismatchError:
            return False


class TokenService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create_access_token(self, principal: UserPrincipal) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": str(principal.user_id),
            "username": principal.username,
            "roles": principal.roles,
            "auth_source": principal.auth_source,
            "iat": now,
            "exp": now + timedelta(minutes=self.settings.jwt_expire_minutes),
        }
        return jwt.encode(
            payload,
            self.settings.app_secret_key.get_secret_value(),
            algorithm=self.settings.jwt_algorithm,
        )

    def decode(self, token: str) -> UserPrincipal:
        try:
            payload = jwt.decode(
                token,
                self.settings.app_secret_key.get_secret_value(),
                algorithms=[self.settings.jwt_algorithm],
            )
            return UserPrincipal(
                user_id=UUID(payload["sub"]),
                username=payload["username"],
                roles=list(payload.get("roles", [])),
                auth_source=payload.get("auth_source", "local"),
            )
        except (jwt.PyJWTError, KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired access token",
            ) from exc


bearer = HTTPBearer(auto_error=False)


def principal_dependency(token_service: TokenService):
    async def get_principal(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> UserPrincipal:
        if credentials is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return token_service.decode(credentials.credentials)

    return get_principal
