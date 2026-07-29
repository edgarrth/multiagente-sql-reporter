from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from axiz.pe.sql_agent.container import ApplicationContainer
from axiz.pe.sql_agent.dependencies import get_container, get_current_principal
from axiz.pe.sql_agent.models.contracts import (
    ChatMessageResponse,
    SessionCreateRequest,
    SessionDeleteResponse,
    SessionResponse,
    SessionTokenUsage,
    SessionUpdateRequest,
    UserPrincipal,
)

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


@router.patch("/{session_id}", response_model=SessionResponse)
async def rename_session(
    session_id: UUID,
    request: SessionUpdateRequest,
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> SessionResponse:
    try:
        row = await container.sessions.rename(session_id, principal.user_id, request.title)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SessionResponse.model_validate(row)


@router.delete("/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(
    session_id: UUID,
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> SessionDeleteResponse:
    try:
        row = await container.sessions.delete(session_id, principal.user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SessionDeleteResponse.model_validate(row)


@router.get("/{session_id}/usage", response_model=SessionTokenUsage)
async def get_session_usage(
    session_id: UUID,
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> SessionTokenUsage:
    try:
        payload = await container.sessions.get_usage(session_id, principal.user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SessionTokenUsage.model_validate(payload)


@router.get("/{session_id}/messages", response_model=list[ChatMessageResponse])
async def list_messages(
    session_id: UUID,
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> list[ChatMessageResponse]:
    try:
        rows = await container.sessions.list_messages(session_id, principal.user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [ChatMessageResponse.model_validate(row) for row in rows]
