from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from axiz.pe.sql_agent.container import ApplicationContainer
from axiz.pe.sql_agent.dependencies import get_container, get_current_principal
from axiz.pe.sql_agent.models.contracts import (
    AgentRunRequest,
    HumanFeedbackRequest,
    RunResponse,
    UserPrincipal,
)

from axiz.pe.sql_agent.services.sse import encode_sse

router = APIRouter(prefix="/api/v1/agent/runs", tags=["agent"])


async def _as_sse(events: AsyncIterator[dict]) -> AsyncIterator[str]:
    yield ": stream-open\n\n"
    async for event in events:
        yield encode_sse(event)
    yield ": stream-closed\n\n"


@router.post("", response_model=RunResponse, status_code=202)
async def start_run(
    request: AgentRunRequest,
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> RunResponse:
    try:
        return await container.workflow.start_run(
            principal=principal,
            session_id=request.session_id,
            question=request.question,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/stream", response_class=StreamingResponse, status_code=200)
async def stream_run(
    request: AgentRunRequest,
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> StreamingResponse:
    try:
        events = container.workflow.stream_start_run(
            principal=principal,
            session_id=request.session_id,
            question=request.question,
        )
        return StreamingResponse(
            _as_sse(events),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{run_id}/feedback", response_model=RunResponse)
async def resume_run(
    run_id: UUID,
    request: HumanFeedbackRequest,
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> RunResponse:
    try:
        return await container.workflow.resume_run(
            principal=principal,
            run_id=run_id,
            feedback=request,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/feedback/stream", response_class=StreamingResponse)
async def stream_resume_run(
    run_id: UUID,
    request: HumanFeedbackRequest,
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> StreamingResponse:
    try:
        events = container.workflow.stream_resume_run(
            principal=principal,
            run_id=run_id,
            feedback=request,
        )
        return StreamingResponse(
            _as_sse(events),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: UUID,
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> RunResponse:
    try:
        return await container.workflow.get_run(principal, run_id)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
