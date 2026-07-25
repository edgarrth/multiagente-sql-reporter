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
