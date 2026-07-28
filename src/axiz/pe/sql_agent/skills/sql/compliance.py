from __future__ import annotations

import json
from typing import Any

from axiz.pe.sql_agent.models.contracts import (
    FeedbackSemanticComplianceOutput,
    SqlFeedbackPlan,
    SqlGenerationOutput,
)
from axiz.pe.sql_agent.services.llm import StructuredLLM
from axiz.pe.sql_agent.tools.sql_revision_diff import SqlRevisionDiffAnalyzer


class FeedbackComplianceSkill:
    """Semantically verify that a regenerated SQL proposal honors HITL feedback."""

    def __init__(self, llm: StructuredLLM, dialect: str = "postgres") -> None:
        self.llm = llm
        self.diff_analyzer = SqlRevisionDiffAnalyzer(dialect=dialect)

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

For temporal plans, enforce the declared time_window_scope:
- comparison_baseline must preserve the current period and use exactly comparison_periods
  immediately preceding closed periods on the comparison side.
- comparison_series must expose the requested number of separate period buckets.
- Merely widening WHERE is not compliant when projected comparative metrics remain unchanged.
- overall_window changes only the complete governed range.

When feedback_plan has strategy=regenerate, a raw_user_message and no typed changes, evaluate the
whole raw message directly against previous_sql, final_sql and sql_ast_diff. Use the identifier
"revision" in applied_changes or missing_changes. Do not require the request to fit a predefined
filter, date, metric or projection schema.

For legacy typed plans, return missing_changes using the exact change_id values. Return
unexpected_changes only when the final SQL alters an unrelated element that the user did not ask
to change. Set compliant=false whenever the requested revision is incomplete or an unrelated
change can materially affect the answer. Ask for clarification only when the user request is
inherently ambiguous, not when the generator simply failed to apply it.
""".strip()
        payload = {
            "feedback_plan": plan.model_dump(mode="json"),
            "previous_sql": previous_sql,
            "final_sql": final_sql,
            "generated_contract": generated.model_dump(mode="json"),
            "governed_application": governed_application,
            "sql_ast_diff": self.diff_analyzer.compare(previous_sql, final_sql),
            "semantic_context": semantic_context,
        }
        return await self.llm.parse(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, default=str),
            response_model=FeedbackSemanticComplianceOutput,
        )
