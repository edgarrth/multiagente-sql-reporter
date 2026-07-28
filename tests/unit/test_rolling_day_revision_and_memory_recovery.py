from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

from axiz.pe.sql_agent.models.contracts import (
    ConversationMemory,
    RunResponse,
    RunStatus,
)
from axiz.pe.sql_agent.services.conversation_memory import StructuredConversationMemoryService
from axiz.pe.sql_agent.skills.sql.feedback_planning import FeedbackPlanningSkill
from axiz.pe.sql_agent.tools.sql_feedback_plan import SqlFeedbackPlanValidator

ROLLING_DAY_SQL = """
SELECT transaction_id, transaction_timestamp, transaction_date, status
FROM semantic.v_payment_transactions
WHERE status = 'REVERSED'
  AND transaction_date >= CAST(TIMEZONE('America/Lima', CURRENT_TIMESTAMP) AS DATE) - 7
  AND transaction_date < CAST(TIMEZONE('America/Lima', CURRENT_TIMESTAMP) AS DATE)
ORDER BY transaction_timestamp DESC
LIMIT 500
"""


class UnusedLlm:
    def __init__(self) -> None:
        self.calls = 0

    async def parse(self, **kwargs):  # pragma: no cover
        self.calls += 1
        raise AssertionError("Separate feedback interpretation must not be called")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "feedback",
    [
        "aumentale 7 dias a la busqueda",
        "quiero que la busqueda sea de los ultimos 14 dias",
    ],
)
async def test_day_feedback_is_forwarded_verbatim_without_regex_or_fixed_fields(
    feedback: str,
) -> None:
    llm = UnusedLlm()
    skill = FeedbackPlanningSkill(llm, 500, SqlFeedbackPlanValidator())
    plan = await skill.interpret(
        feedback=feedback,
        previous_sql=ROLLING_DAY_SQL,
        semantic_context={},
        current_contract={"sources": ["semantic.v_payment_transactions"]},
    )
    assert llm.calls == 0
    assert plan.changes == []
    assert plan.raw_user_message == feedback
    assert plan.requires_clarification is False


def test_agent_and_skill_layers_contain_no_natural_language_regex() -> None:
    roots = [
        Path("src/axiz/pe/sql_agent/agents"),
        Path("src/axiz/pe/sql_agent/skills"),
    ]
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for root in roots
        for path in root.rglob("*.py")
    )
    assert "import re" not in sources
    assert "re.compile" not in sources


def test_only_four_agent_classes_exist() -> None:
    agent_files = sorted(
        path.name
        for path in Path("src/axiz/pe/sql_agent/agents").glob("*_agent.py")
    )
    assert agent_files == [
        "domain_analyst_agent.py",
        "evidence_reviewer_agent.py",
        "investigation_coordinator_agent.py",
        "sql_engineer_agent.py",
    ]


def test_failed_follow_up_preserves_last_valid_sql_baseline() -> None:
    service = StructuredConversationMemoryService()
    current = ConversationMemory(last_sql=ROLLING_DAY_SQL, last_limit=500)
    response = RunResponse(
        run_id=uuid4(), session_id=uuid4(), status=RunStatus.FAILED, error="revision failed"
    )
    merged = service.merge(
        current,
        {
            "intent": "analytical_query",
            "question": "aumentale 7 dias a la busqueda",
            "context_resolution": {"relation": "analytical_follow_up"},
            "feedback_plan": {
                "feedback": "aumentale 7 dias a la busqueda",
                "changes": [
                    {
                        "change_id": "change_1",
                        "change_type": "change_time_window",
                        "time_window_delta_days": 7,
                    }
                ],
            },
        },
        response,
    )
    assert merged.last_sql == ROLLING_DAY_SQL
    assert merged.last_limit == 500
    assert merged.pending_revision_plan["changes"][0]["time_window_delta_days"] == 7


@pytest.mark.skipif(importlib.util.find_spec("sqlglot") is None, reason="SQLGlot unavailable")
def test_day_delta_is_applied_to_ast_and_preserves_limit() -> None:
    from axiz.pe.sql_agent.models.contracts import SqlChangeRequest, SqlFeedbackPlan

    plan = SqlFeedbackPlan(
        changes=[
            SqlChangeRequest(
                change_id="change_1",
                change_type=SqlChangeType.CHANGE_TIME_WINDOW,
                time_window_delta_days=7,
                deterministic_candidate=True,
            )
        ]
    )
    application = SqlFeedbackApplier("postgres", 500).apply(
        ROLLING_DAY_SQL, plan, previous_sql=ROLLING_DAY_SQL
    )
    assert application.previous_time_window_days == 7
    assert application.applied_time_window_days == 14
    assert "LIMIT 500" in application.sql.upper()
