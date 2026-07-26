from uuid import uuid4

from axiz.pe.sql_agent.services.sse import encode_sse
from axiz.pe.sql_agent.models.contracts import (
    ApprovalDecision,
    HumanFeedbackRequest,
    ReviewPayload,
    SessionResponse,
)
from axiz.pe.sql_agent.services.message_format import feedback_content


def test_sse_encoder_emits_event_and_json_payload() -> None:
    payload = encode_sse({"type": "stage", "data": {"node": "classify", "label": "OK"}})
    assert payload.startswith("event: stage\n")
    assert '"node": "classify"' in payload
    assert payload.endswith("\n\n")


def test_review_revision_distinguishes_revised_sql_messages() -> None:
    review = ReviewPayload(
        run_id=uuid4(),
        revision=2,
        question="¿Cuál fue la facturación?",
        domain="acquiring",
        interpretation="Monto procesado del último mes cerrado",
        sql="SELECT 1",
        assumptions=[],
        source_objects=["semantic.v_monthly_payment_metrics"],
    )
    assert review.revision == 2


def test_feedback_message_is_persistable_as_new_chat_turn() -> None:
    feedback = HumanFeedbackRequest(
        decision=ApprovalDecision.REQUEST_CHANGES,
        comment="Usa el último mes cerrado",
    )
    content = feedback_content(feedback)
    assert content.startswith("Solicité cambios")
    assert "último mes cerrado" in content


def test_session_contract_exposes_pending_run_and_message_count() -> None:
    session = SessionResponse(
        id=uuid4(),
        title="Facturación por comercio",
        created_at="2026-07-25T10:00:00-05:00",
        updated_at="2026-07-25T10:05:00-05:00",
        pending_run_id=uuid4(),
        message_count=4,
    )
    assert session.pending_run_id is not None
    assert session.message_count == 4


def test_run_response_supports_safe_execution_trace() -> None:
    from axiz.pe.sql_agent.models.contracts import AgentTraceStep, RunResponse, RunStatus

    response = RunResponse(
        run_id=uuid4(),
        session_id=uuid4(),
        status=RunStatus.COMPLETED,
        answer="Respuesta",
        trace=[
            AgentTraceStep(
                stage="classify",
                label="Intención y dominio",
                detail="Resumen auditable",
                summary={"domain": "acquiring"},
            )
        ],
    )
    assert response.trace[0].summary["domain"] == "acquiring"


def test_session_delete_contract_is_explicit() -> None:
    from axiz.pe.sql_agent.models.contracts import SessionDeleteResponse

    deleted = SessionDeleteResponse(id=uuid4())
    assert deleted.deleted is True


def test_run_response_exposes_security_and_cost_validation() -> None:
    from axiz.pe.sql_agent.models.contracts import (
        CostValidation,
        RunResponse,
        RunStatus,
        SecurityValidation,
    )

    response = RunResponse(
        run_id=uuid4(),
        session_id=uuid4(),
        status=RunStatus.COMPLETED,
        security_validation=SecurityValidation(
            approved=True,
            statement_type="SELECT",
            max_rows=500,
            enforced_limit=500,
            tables=["semantic.v_daily_payment_metrics"],
        ),
        cost_validation=CostValidation(
            approved=True,
            total_cost=120.5,
            max_plan_cost=150000,
            plan_rows=30,
            max_plan_rows=250000,
            relation_bytes=1024,
            max_relation_bytes=536870912,
            timeout_seconds=20,
        ),
    )
    assert response.security_validation is not None
    assert response.security_validation.enforced_limit == 500
    assert response.cost_validation is not None
    assert response.cost_validation.max_plan_rows == 250000


def test_cost_tool_extracts_physical_relations_from_explain_plan() -> None:
    import pytest

    pytest.importorskip("psycopg")
    from axiz.pe.sql_agent.tools.sql_executor import PostgresQueryTool

    plan = [
        {
            "Plan": {
                "Node Type": "Hash Join",
                "Plans": [
                    {"Node Type": "Seq Scan", "Schema": "analytics", "Relation Name": "fact_payment_transactions"},
                    {"Node Type": "Seq Scan", "Schema": "analytics", "Relation Name": "dim_merchant"},
                ],
            }
        }
    ]
    relations = PostgresQueryTool._plan_relations(plan)
    assert relations == {
        "analytics.fact_payment_transactions",
        "analytics.dim_merchant",
    }
