from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from axiz.pe.sql_agent.container import ApplicationContainer
from axiz.pe.sql_agent.dependencies import get_container, get_current_principal
from axiz.pe.sql_agent.models.contracts import (
    AgentRunRequest,
    HumanFeedbackRequest,
    RunResponse,
    UserPrincipal,
)

router = APIRouter(prefix="/api/v1/agent/runs", tags=["agent"])


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
