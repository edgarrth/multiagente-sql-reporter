from __future__ import annotations

import json
from typing import Any

from axiz.pe.sql_agent.models.contracts import (
    FeedbackSemanticComplianceOutput,
    SqlFeedbackPlan,
    SqlGenerationOutput,
)
from axiz.pe.sql_agent.services.llm import StructuredLLM


class FeedbackComplianceAgent:
    """Semantically verify that a regenerated SQL proposal honors HITL feedback."""

    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    async def validate(
        self,
        *,
        plan: SqlFeedbackPlan,
        previous_sql: str,
        generated: SqlGenerationOutput,
        final_sql: str,
        semantic_context: dict[str, Any],
        governed_application: dict[str, Any],
    ) -> FeedbackSemanticComplianceOutput:
        system = """
You are an independent reviewer of a governed Text-to-SQL revision. Determine whether the new
proposal applies every required change from feedback_plan while preserving prior analytical
semantics that were not requested to change.

Evaluate business meaning, selected metrics, dimensions, filters, period, grouping, ordering,
limit and semantic sources. Do not judge SQL security or query cost; separate deterministic gates
handle those controls. Do not assume a change was applied merely because the interpretation says
so: use final_sql and generated_contract as evidence.

When governed_application reports that a requested value was clamped by policy (for example a
LIMIT above MAX_RESULT_ROWS), evaluate compliance against the applied governed value and report
the policy adjustment as applied rather than missing.

Return missing_changes using the exact change_id values from feedback_plan. Return
unexpected_changes when the proposal alters an unrelated metric, dimension, filter, time window
or source. Set compliant=false whenever a required change is missing or an unrelated semantic
change can materially affect the answer. Ask for clarification only when the user request is
inherently ambiguous, not when the SQL generator simply failed to apply it.
""".strip()
        payload = {
            "feedback_plan": plan.model_dump(mode="json"),
            "previous_sql": previous_sql,
            "final_sql": final_sql,
            "generated_contract": generated.model_dump(mode="json"),
            "governed_application": governed_application,
            "semantic_context": semantic_context,
        }
        return await self.llm.parse(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, default=str),
            response_model=FeedbackSemanticComplianceOutput,
        )
