from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from axiz.pe.sql_agent.container import ApplicationContainer
from axiz.pe.sql_agent.core.auth import bearer
from axiz.pe.sql_agent.models.contracts import UserPrincipal


def get_container(request: Request) -> ApplicationContainer:
    return request.app.state.container


async def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> UserPrincipal:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    container: ApplicationContainer = request.app.state.container
    return container.tokens.decode(credentials.credentials)


async def verify_internal_service(
    request: Request,
    x_internal_service_key: str | None = Header(default=None),
) -> None:
    container: ApplicationContainer = request.app.state.container
    expected = container.settings.internal_service_key.get_secret_value()
    if not x_internal_service_key or x_internal_service_key != expected:
        raise HTTPException(status_code=401, detail="Invalid internal service key")
