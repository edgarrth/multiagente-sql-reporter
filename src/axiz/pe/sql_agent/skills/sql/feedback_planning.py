from __future__ import annotations

from typing import Any

import structlog

from axiz.pe.sql_agent.models.contracts import SqlFeedbackPlan, SqlFeedbackStrategy
from axiz.pe.sql_agent.services.llm import StructuredLLM
from axiz.pe.sql_agent.services.semantic_query_spec import SemanticQuerySpecService
from axiz.pe.sql_agent.tools.sql_feedback_plan import SqlFeedbackPlanValidator

logger = structlog.get_logger(__name__)


class FeedbackPlanningSkill:
    """Create a generic, SQL-native revision envelope.

    The previous implementation translated natural language into a closed vocabulary of filter,
    projection, metric and time-window operations before the SQL engineer saw the request. That
    made the workflow fragile: every new kind of edit required another target, field or compiler
    branch. The generic path intentionally keeps the complete previous SQL and the complete user
    message as the revision contract. The SQL Engineer LLM performs the semantic edit in one pass;
    SQLGlot, catalog, security, cost and HITL remain deterministic gates afterwards.

    ``SqlFeedbackPlan`` is retained as a workflow envelope for backward compatibility, but the
    active SQL-native path leaves ``changes`` empty. The complete raw message is the revision
    contract; no filter/date/metric/projection target is manufactured before the SQL Engineer sees it.
    """

    def __init__(
        self,
        llm: StructuredLLM,
        max_result_rows: int,
        plan_validator: SqlFeedbackPlanValidator,
        dialect: str = "postgres",
    ) -> None:
        # ``llm`` remains in the constructor so existing dependency wiring is compatible. Feedback
        # understanding now happens in SqlGenerationSkill._revise, where the model sees the full SQL.
        self.llm = llm
        self.max_result_rows = max_result_rows
        self.plan_validator = plan_validator
        self.dialect = dialect
        self.query_specs = SemanticQuerySpecService(dialect=dialect)

    async def interpret(
        self,
        *,
        feedback: str,
        previous_sql: str,
        semantic_context: dict[str, Any],
        current_contract: dict[str, Any],
    ) -> SqlFeedbackPlan:
        message = (feedback or "").strip()
        if not message:
            return SqlFeedbackPlan(
                feedback="",
                summary="No se recibió una instrucción de revisión.",
                strategy=SqlFeedbackStrategy.CLARIFICATION,
                requires_regeneration=False,
                requires_clarification=True,
                clarification_question="Indica qué deseas cambiar en la consulta.",
                confidence=0.0,
            )
        if not (previous_sql or "").strip():
            return SqlFeedbackPlan(
                feedback=message,
                summary="No existe una consulta SQL anterior para revisar.",
                strategy=SqlFeedbackStrategy.CLARIFICATION,
                requires_regeneration=False,
                requires_clarification=True,
                clarification_question="Genera primero una consulta y luego solicita el cambio.",
                confidence=0.0,
            )

        base_spec = self.query_specs.from_contract(
            current_contract,
            previous_sql=previous_sql,
            original_question=str(current_contract.get("original_question") or ""),
            raw_user_message=message,
        )
        plan = SqlFeedbackPlan(
            feedback=message,
            raw_user_message=message,
            summary=message,
            strategy=SqlFeedbackStrategy.REGENERATE,
            changes=[],
            requires_regeneration=True,
            requires_clarification=False,
            confidence=1.0,
            query_spec_ref=base_spec.reference,
        )
        logger.info(
            "sql_native_feedback_envelope_created",
            query_spec_id=base_spec.spec_id,
            query_spec_version=base_spec.version,
            feedback_length=len(message),
        )
        return plan
