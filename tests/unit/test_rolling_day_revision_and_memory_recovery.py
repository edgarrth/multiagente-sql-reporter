from __future__ import annotations

from uuid import uuid4
import importlib.util

import pytest

from axiz.pe.sql_agent.agents.feedback_interpreter_agent import FeedbackInterpreterAgent
from axiz.pe.sql_agent.models.contracts import (
    ConversationMemory,
    RunResponse,
    RunStatus,
    SqlChangeType,
)
from axiz.pe.sql_agent.services.conversation_memory import StructuredConversationMemoryService
from axiz.pe.sql_agent.tools.sql_feedback import SqlFeedbackApplier


ROLLING_DAY_SQL = """
SELECT
  m.merchant_id,
  m.merchant_name,
  CAST(TIMEZONE('America/Lima', CURRENT_TIMESTAMP) AS DATE) - 30 AS period_start_date,
  CAST(TIMEZONE('America/Lima', CURRENT_TIMESTAMP) AS DATE) AS period_end_date,
  SUM(m.failed_settlement_count) AS failed_settlement_count
FROM semantic.v_merchant_settlement_metrics AS m
WHERE m.metric_date >= CAST(TIMEZONE('America/Lima', CURRENT_TIMESTAMP) AS DATE) - 30
  AND m.metric_date < CAST(TIMEZONE('America/Lima', CURRENT_TIMESTAMP) AS DATE)
GROUP BY m.merchant_id, m.merchant_name
ORDER BY failed_settlement_count DESC
LIMIT 12
"""


def test_explicit_day_delta_is_planned_without_llm() -> None:
    plan = FeedbackInterpreterAgent._deterministic_structural_plan(
        "agregale 15 dias a la busqueda",
        previous_sql=ROLLING_DAY_SQL,
    )
    assert plan is not None
    assert len(plan.changes) == 1
    change = plan.changes[0]
    assert change.change_type == SqlChangeType.CHANGE_TIME_WINDOW
    assert change.time_window_delta_days == 15
    assert change.deterministic_candidate is True


def test_unitless_delta_inherits_only_verified_previous_window_unit() -> None:
    plan = FeedbackInterpreterAgent._deterministic_structural_plan(
        "agregale 15 a la busqueda de liquidaciones",
        previous_sql=ROLLING_DAY_SQL,
    )
    assert plan is not None
    change = plan.changes[0]
    assert change.time_window_delta_days == 15
    assert change.time_window_delta_months is None


def test_unitless_delta_does_not_override_limit_semantics() -> None:
    plan = FeedbackInterpreterAgent._deterministic_structural_plan(
        "agregale 15 registros a la busqueda de liquidaciones",
        previous_sql=ROLLING_DAY_SQL,
    )
    assert plan is None


def test_failed_follow_up_preserves_last_valid_sql_baseline() -> None:
    service = StructuredConversationMemoryService()
    current = ConversationMemory(
        last_sql=ROLLING_DAY_SQL,
        last_interpretation="Ranking de comercios durante los últimos 30 días.",
        last_domain="acquiring",
        last_limit=12,
        last_resolved_question="Lista comercios con fallas de liquidación.",
    )
    response = RunResponse(
        run_id=uuid4(),
        session_id=uuid4(),
        status=RunStatus.FAILED,
        error="No se pudo aplicar el cambio temporal",
    )
    merged = service.merge(
        current,
        {
            "intent": "analytical_query",
            "question": "agregale 15 dias a la busqueda",
            "context_resolution": {"relation": "analytical_follow_up"},
            "feedback_plan": {
                "feedback": "agregale 15 dias a la busqueda",
                "changes": [
                    {
                        "change_id": "change_1",
                        "change_type": "change_time_window",
                        "time_window_delta_days": 15,
                    }
                ],
            },
            "interpretation": "Ventana de 45 días",
        },
        response,
    )
    assert merged.last_sql == ROLLING_DAY_SQL
    assert merged.last_limit == 12
    assert merged.last_attempt_status == RunStatus.FAILED.value
    assert merged.pending_revision_feedback == "agregale 15 dias a la busqueda"
    assert merged.pending_revision_plan["changes"][0]["time_window_delta_days"] == 15


def test_restore_from_structured_payload_repairs_legacy_memory() -> None:
    service = StructuredConversationMemoryService()
    restored = service.restore_from_payload(
        ConversationMemory(),
        {
            "run_id": str(uuid4()),
            "status": "awaiting_approval",
            "resolved_question": "Lista comercios con fallas de liquidación",
            "interpretation": "Ranking de comercios en 30 días",
            "domain": "acquiring",
            "sql": ROLLING_DAY_SQL,
        },
    )
    assert restored.last_sql == ROLLING_DAY_SQL.strip()
    assert restored.last_limit == 12
    assert restored.last_domain == "acquiring"


def test_specialist_graph_fails_closed_before_security_when_revision_has_no_sql() -> None:
    from pathlib import Path

    source = Path(
        "src/axiz/pe/sql_agent/workflow/subgraphs/specialist.py"
    ).read_text(encoding="utf-8")
    assert "route_after_deterministic_revision" in source
    assert 'not str(state.get("final_sql") or "").strip()' in source
    assert 'state["final_sql"]' not in source[source.index("async def validate_security"):source.index("async def estimate_cost")]


@pytest.mark.skipif(
    importlib.util.find_spec("sqlglot") is None,
    reason="SQLGlot unavailable",
)
def test_day_delta_is_applied_to_ast_and_preserves_limit() -> None:
    plan = FeedbackInterpreterAgent._deterministic_structural_plan(
        "agregale 15 dias a la busqueda",
        previous_sql=ROLLING_DAY_SQL,
    )
    assert plan is not None
    application = SqlFeedbackApplier("postgres", max_rows=500).apply(
        ROLLING_DAY_SQL,
        plan,
        previous_sql=ROLLING_DAY_SQL,
    )
    normalized = " ".join(application.sql.upper().split())
    assert application.previous_time_window_days == 30
    assert application.applied_time_window_days == 45
    assert "LIMIT 12" in normalized
    assert "- 45" in normalized or "INTERVAL '45 DAY" in normalized
