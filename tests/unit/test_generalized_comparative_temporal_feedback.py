from __future__ import annotations

import pytest

from axiz.pe.sql_agent.models.contracts import SqlFeedbackStrategy
from axiz.pe.sql_agent.skills.sql.feedback_planning import FeedbackPlanningSkill
from axiz.pe.sql_agent.tools.sql_feedback_plan import SqlFeedbackPlanValidator
from axiz.pe.sql_agent.tools.temporal_query_shape import (
    TemporalQueryShapeAnalyzer,
    TemporalQueryTopology,
)

COMPARATIVE_SQL = """
WITH monthly AS (
  SELECT card_scheme,
    SUM(processed_amount_pen) FILTER (
      WHERE metric_month = DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'
    ) AS current_amount,
    SUM(processed_amount_pen) FILTER (
      WHERE metric_month = DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '2 months'
    ) AS previous_amount
  FROM semantic.v_monthly_payment_metrics
  WHERE metric_month >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '2 months'
    AND metric_month < DATE_TRUNC('month', CURRENT_DATE)
  GROUP BY card_scheme
)
SELECT * FROM monthly LIMIT 300
"""


class UnusedLlm:
    async def parse(self, **kwargs):  # pragma: no cover
        raise AssertionError("The full-SQL revision agent owns semantic interpretation")


@pytest.mark.skipif(
    __import__("importlib.util").util.find_spec("sqlglot") is None,
    reason="SQLGlot unavailable",
)
def test_temporal_shape_detects_comparative_buckets_from_ast() -> None:
    shape = TemporalQueryShapeAnalyzer.analyze(COMPARATIVE_SQL)
    assert shape.topology == TemporalQueryTopology.COMPARATIVE_BUCKETS
    assert shape.grain == "month"
    assert shape.overall_periods == 2
    assert shape.bucket_offsets == (1, 2)


@pytest.mark.asyncio
async def test_comparative_request_is_forwarded_verbatim_to_full_sql_revision() -> None:
    skill = FeedbackPlanningSkill(UnusedLlm(), 500, SqlFeedbackPlanValidator())
    message = "haz que sea 2 meses en vez de solo el mes anterior"
    plan = await skill.interpret(
        feedback=message,
        previous_sql=COMPARATIVE_SQL,
        semantic_context={},
        current_contract={},
    )
    assert plan.strategy == SqlFeedbackStrategy.REGENERATE
    assert plan.changes == []
    assert plan.raw_user_message == message


@pytest.mark.asyncio
async def test_potential_ambiguity_is_resolved_by_revision_agent_with_full_sql_context() -> None:
    skill = FeedbackPlanningSkill(UnusedLlm(), 500, SqlFeedbackPlanValidator())
    plan = await skill.interpret(
        feedback="agrega un mes más",
        previous_sql=COMPARATIVE_SQL,
        semantic_context={},
        current_contract={},
    )
    assert plan.requires_clarification is False
    assert plan.requires_regeneration is True
    assert plan.raw_user_message == "agrega un mes más"
