from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response, StreamingResponse

from axiz.pe.sql_agent.container import ApplicationContainer
from axiz.pe.sql_agent.dependencies import get_container, get_current_principal
from axiz.pe.sql_agent.models.contracts import (
    AgentRunRequest,
    HumanFeedbackRequest,
    QueryResult,
    RunCancelResponse,
    RunResponse,
    UserPrincipal,
)

from axiz.pe.sql_agent.repositories.run_repository import RunConflictError
from axiz.pe.sql_agent.services.sse import encode_sse

router = APIRouter(prefix="/api/v1/agent/runs", tags=["agent"])


async def _as_sse(events: AsyncIterator[dict]) -> AsyncIterator[str]:
    yield ": stream-open\n\n"
    try:
        async for event in events:
            yield encode_sse(event)
    except RunConflictError as exc:
        yield encode_sse(
            {
                "type": "conflict",
                "data": {
                    "message": str(exc),
                    "run_id": str(exc.run_id) if exc.run_id else None,
                    "status": exc.status,
                },
            }
        )
    yield ": stream-closed\n\n"


@router.post("", response_model=RunResponse, status_code=202)
async def start_run(
    request: AgentRunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> RunResponse:
    try:
        return await container.workflow.start_run(
            principal=principal,
            session_id=request.session_id,
            question=request.question,
            idempotency_key=idempotency_key or request.idempotency_key,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RunConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "run_id": str(exc.run_id) if exc.run_id else None,
                "status": exc.status,
            },
        ) from exc


@router.post("/stream", response_class=StreamingResponse, status_code=200)
async def stream_run(
    request: AgentRunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> StreamingResponse:
    try:
        events = container.workflow.stream_start_run(
            principal=principal,
            session_id=request.session_id,
            question=request.question,
            idempotency_key=idempotency_key or request.idempotency_key,
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
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> RunResponse:
    try:
        return await container.workflow.resume_run(
            principal=principal,
            run_id=run_id,
            feedback=request,
            idempotency_key=idempotency_key or request.idempotency_key,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RunConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/feedback/stream", response_class=StreamingResponse)
async def stream_resume_run(
    run_id: UUID,
    request: HumanFeedbackRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> StreamingResponse:
    try:
        events = container.workflow.stream_resume_run(
            principal=principal,
            run_id=run_id,
            feedback=request,
            idempotency_key=idempotency_key or request.idempotency_key,
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
    except (ValueError, RunConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/cancel", response_model=RunCancelResponse)
async def cancel_run(
    run_id: UUID,
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> RunCancelResponse:
    row = await container.runs.request_cancel(run_id, principal.user_id)
    if not row:
        existing = await container.runs.get(run_id, principal.user_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Run not found")
        raise HTTPException(
            status_code=409,
            detail=f"Run cannot be cancelled from status {existing['status']}",
        )
    return RunCancelResponse(
        run_id=run_id, status=str(row["status"]), cancel_requested=True
    )


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


@router.get("/{run_id}/exports/excel")
async def export_run_excel(
    run_id: UUID,
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> Response:
    row = await container.runs.get(run_id, principal.user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Run not found")

    state = row.get("state") or {}
    payload = state.get("_api_response") or {}
    result_payload = payload.get("result") or state.get("query_result")
    if not result_payload:
        raise HTTPException(status_code=409, detail="The run has no tabular result to export")

    result = QueryResult.model_validate(result_payload)
    availability = container.excel_exports.availability(result, str(row.get("status")))
    if not availability.available:
        raise HTTPException(status_code=409, detail=availability.reason)

    sql = str(payload.get("sql") or state.get("generated_sql") or "")
    domain = state.get("domain")
    content = container.excel_exports.build(
        result=result,
        run_id=run_id,
        question=str(row.get("question") or "Resultado SQL"),
        sql=sql,
        domain=str(domain) if domain else None,
    )
    filename = container.excel_exports.filename(str(row.get("question") or "resultado-sql"), run_id)
    await container.runs.audit(
        run_id,
        principal.user_id,
        "excel_exported",
        {
            "filename": filename,
            "row_count": result.row_count,
            "truncated": result.truncated,
        },
    )
    return Response(
        content=content,
        media_type=container.excel_exports.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )
