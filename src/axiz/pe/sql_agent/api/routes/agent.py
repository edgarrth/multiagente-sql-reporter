from __future__ import annotations

import asyncio
from contextlib import suppress
from collections.abc import AsyncIterator
from uuid import UUID

import structlog
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
logger = structlog.get_logger(__name__)


async def _as_sse(
    events: AsyncIterator[dict], *, heartbeat_seconds: float = 15.0
) -> AsyncIterator[str]:
    """Encode workflow events and keep long provider calls alive through proxies.

    The pending ``anext`` task is not cancelled when a heartbeat is emitted. Cancelling it
    would also cancel the underlying agent generator and could leave an approved run without
    a terminal response.
    """
    yield ": stream-open\n\n"
    iterator = events.__aiter__()
    pending: asyncio.Task | None = None
    try:
        pending = asyncio.create_task(anext(iterator))
        while True:
            done, _ = await asyncio.wait(
                {pending}, timeout=max(0.05, float(heartbeat_seconds))
            )
            if not done:
                yield ": heartbeat\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                break
            yield encode_sse(event)
            pending = asyncio.create_task(anext(iterator))
    except RunConflictError as exc:
        logger.warning(
            "agent_stream_conflict",
            run_id=str(exc.run_id) if exc.run_id else None,
            status=exc.status,
            error=str(exc),
        )
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
    except asyncio.CancelledError:
        logger.warning("agent_stream_client_disconnected")
        raise
    except Exception as exc:
        logger.exception("agent_stream_encoding_failed", error=str(exc))
        yield encode_sse(
            {
                "type": "error",
                "data": {"message": "The agent stream ended unexpectedly."},
            }
        )
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
        aclose = getattr(iterator, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
            except Exception:
                logger.warning("agent_stream_close_failed")
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
            _as_sse(events, heartbeat_seconds=container.settings.sse_heartbeat_seconds),
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
            _as_sse(events, heartbeat_seconds=container.settings.sse_heartbeat_seconds),
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
    autonomous_evidence = list(state.get("autonomous_evidence") or [])
    if autonomous_evidence:
        content = container.excel_exports.build_investigation(
            run_id=run_id,
            question=str(row.get("question") or "Investigación SQL"),
            answer=str(payload.get("answer") or state.get("answer") or ""),
            evidence=autonomous_evidence,
        )
    else:
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
