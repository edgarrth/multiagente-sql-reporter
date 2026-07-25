from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from axiz.pe.sql_agent.container import ApplicationContainer
from axiz.pe.sql_agent.dependencies import get_container, get_current_principal
from axiz.pe.sql_agent.models.contracts import SessionCreateRequest, SessionResponse, UserPrincipal

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(
    request: SessionCreateRequest,
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> SessionResponse:
    row = await container.sessions.create(principal.user_id, request.title)
    return SessionResponse.model_validate(row)


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> list[SessionResponse]:
    return [
        SessionResponse.model_validate(row)
        for row in await container.sessions.list_by_user(principal.user_id)
    ]
