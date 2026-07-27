from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

pytest.importorskip("structlog")
pytest.importorskip("langgraph")

from axiz.pe.sql_agent.api.routes.agent import _as_sse
from axiz.pe.sql_agent.models.contracts import InvestigationEvidence, QueryResult
from axiz.pe.sql_agent.workflow.service import AgentWorkflowService


@pytest.mark.asyncio
async def test_sse_emits_heartbeat_without_cancelling_pending_agent_event() -> None:
    async def slow_events():
        await asyncio.sleep(0.12)
        yield {"type": "completed", "data": {"status": "completed"}}

    chunks: list[str] = []
    async for chunk in _as_sse(slow_events(), heartbeat_seconds=0.05):
        chunks.append(chunk)

    assert any(chunk == ": heartbeat\n\n" for chunk in chunks)
    assert any("event: completed" in chunk for chunk in chunks)
    assert chunks[-1] == ": stream-closed\n\n"


def test_finished_graph_with_evidence_is_recovered_to_completed_response() -> None:
    service = AgentWorkflowService.__new__(AgentWorkflowService)
    evidence = InvestigationEvidence(
        evidence_id="evidence-1",
        task_id="direct-1",
        specialist="acquiring",
        question="Ranking de comercios",
        interpretation="Ranking de los últimos tres meses cerrados",
        sql="SELECT 1",
        domain="acquiring",
        source_objects=["semantic.v_payment_transactions"],
        result=QueryResult(
            columns=["merchant_name"],
            rows=[{"merchant_name": "Comercio 001"}],
            row_count=1,
            elapsed_ms=5.0,
            truncated=False,
        ).model_dump(mode="json"),
        verification={"valid": True, "confidence": 1.0},
        summary="La consulta devolvió un comercio.",
        findings=["Comercio 001 encabeza el resultado."],
    )

    result = service._ensure_terminal_result(
        {
            "status": "running",
            "autonomous_evidence": [evidence.model_dump(mode="json")],
        },
        run_id=uuid4(),
        session_id=uuid4(),
    )

    assert result["status"] == "completed"
    assert result["answer"] == "La consulta devolvió un comercio."
    assert result["query_result"]["row_count"] == 1
    assert result["generated_sql"] == "SELECT 1"


def test_finished_graph_without_interrupt_or_evidence_fails_explicitly() -> None:
    service = AgentWorkflowService.__new__(AgentWorkflowService)
    result = service._ensure_terminal_result(
        {"status": "running", "generated_sql": "SELECT 1"},
        run_id=uuid4(),
        session_id=uuid4(),
    )

    assert result["status"] == "failed"
    assert "sin una interrupción HITL ni un estado terminal" in result["error"]
    assert result["answer"] == "No fue posible completar la solicitud."
