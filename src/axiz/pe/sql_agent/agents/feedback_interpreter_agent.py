from __future__ import annotations

import json
import re
from typing import Any

from axiz.pe.sql_agent.models.contracts import (
    SqlChangeRequest,
    SqlChangeType,
    SqlFeedbackPlan,
    SqlFeedbackStrategy,
)
from axiz.pe.sql_agent.services.llm import StructuredLLM
from axiz.pe.sql_agent.tools.sql_feedback_plan import SqlFeedbackPlanValidator


class FeedbackInterpreterAgent:
    """Translate free-form HITL feedback into a governed semantic change plan."""

    _LIMIT_PATTERNS = (
        re.compile(
            r"(?ix)\b(?:sube|aumenta|incrementa|cambia|ajusta|pon|establece|modifica|reduce|baja)"
            r"[^\n]{0,45}?\b(?:el\s+)?l[ií]mite\b[^\d]{0,15}(\d[\d._,]*)"
        ),
        re.compile(
            r"(?ix)\b(?:l[ií]mite|limit|max(?:imo)?|m[aá]ximo|top)\b"
            r"\s*(?:de|a|en|=|:)?\s*(\d[\d._,]*)\b"
        ),
        re.compile(
            r"(?ix)\b(?:muestra|devuelve|retorna|trae|obt[eé]n)\b[^\n]{0,30}?"
            r"(\d[\d._,]*)\s+(?:filas|registros|resultados)\b"
        ),
    )

    # Fast path for a complete, unambiguous request that changes only LIMIT. Anchoring the
    # expression to the full feedback prevents mixed requests such as "cambia el límite y el
    # filtro" from being incorrectly reduced to a structural-only change.
    _LIMIT_ONLY_PATTERNS = (
        re.compile(
            r"(?ix)^\s*(?:por\s+favor[,;:]?\s*)?"
            r"(?:pon(?:le)?|agrega|añade|establece|define|aplica|cambia|ajusta|modifica)"
            r"\s+(?:un|el)?\s*l[ií]mite\s*(?:de|a|en|=|:)?\s*"
            r"(\d[\d._,]*)\s*(?:filas|registros|resultados)?"
            r"(?:\s+(?:a|en|para)\s+(?:la\s+)?(?:query|consulta|sql))?\s*[.!]?\s*$"
        ),
        re.compile(
            r"(?ix)^\s*(?:por\s+favor[,;:]?\s*)?limita\s+"
            r"(?:la\s+)?(?:query|consulta|sql)\s+(?:a|en)\s*"
            r"(\d[\d._,]*)\s*(?:filas|registros|resultados)?\s*[.!]?\s*$"
        ),
        re.compile(
            r"(?ix)^\s*(?:por\s+favor[,;:]?\s*)?"
            r"(?:muestra|devuelve|retorna|trae|obt[eé]n)\s+"
            r"(?:como\s+m[aá]ximo\s+)?(\d[\d._,]*)\s+"
            r"(?:filas|registros|resultados)\s*[.!]?\s*$"
        ),
        re.compile(
            r"(?ix)^\s*(?:por\s+favor[,;:]?\s*)?"
            r"(?:top|limit|l[ií]mite)\s*(?:de|a|en|=|:)?\s*"
            r"(\d[\d._,]*)\s*(?:filas|registros|resultados)?\s*[.!]?\s*$"
        ),
    )

    def __init__(
        self,
        llm: StructuredLLM,
        max_result_rows: int,
        plan_validator: SqlFeedbackPlanValidator,
    ) -> None:
        self.llm = llm
        self.max_result_rows = max_result_rows
        self.plan_validator = plan_validator

    async def interpret(
        self,
        *,
        feedback: str,
        previous_sql: str,
        semantic_context: dict[str, Any],
        current_contract: dict[str, Any],
    ) -> SqlFeedbackPlan:
        deterministic = self._deterministic_limit_only_plan(feedback)
        if deterministic is not None:
            return self.plan_validator.validate(deterministic, semantic_context)

        system = """
You are a senior semantic analytics change planner. Convert human feedback about an existing
SQL proposal into a complete typed change plan. Identify every requested change; never reduce
multi-part feedback to only one change.

Use only semantic fields, metrics and sources present in semantic_context. Prefer canonical
column/metric names in target. Do not generate SQL. Classify each requested change as one of:
set_limit, add_filter, remove_filter, replace_filter, change_time_window, add_dimension,
remove_dimension, change_grouping, change_order, add_metric, remove_metric, replace_metric,
replace_source, semantic_regeneration.

Rules:
- Simple LIMIT, ORDER BY and basic filter changes are deterministic candidates.
- Metrics, dimensions, grouping, source selection, complex periods and business-meaning changes
  require regeneration, even if a later AST step can enforce part of the request.
- Preserve every aspect of the previous analytical contract not explicitly changed.
- Use requires_clarification only when the request cannot be resolved from the previous contract
  and semantic catalog. Do not ask for clarification merely because the feedback is concise.
- For filters, return target, operator and value/values. Use canonical SQL operators such as =,
  !=, >, >=, <, <=, IN, NOT IN, LIKE, ILIKE, IS NULL and IS NOT NULL.
- For ordering, return target and asc/desc.
- For LIMIT, return the exact requested integer. The platform will apply its governed maximum.
- semantic_regeneration is reserved for changes such as “haz una comparación más justa” or
  “usa una métrica que represente mejor la facturación”; explain the intended business change.
- change_id values must be stable short identifiers such as change_1, change_2.
""".strip()
        payload = {
            "human_feedback": feedback,
            "previous_sql": previous_sql,
            "current_contract": current_contract,
            "semantic_context": semantic_context,
            "max_allowed_rows": self.max_result_rows,
        }
        plan = await self.llm.parse(
            system=system,
            user=json.dumps(payload, ensure_ascii=False, default=str),
            response_model=SqlFeedbackPlan,
        )
        plan.feedback = feedback
        plan = self._merge_deterministic_limit(plan, feedback)
        if not plan.changes and not plan.requires_clarification:
            plan.changes = [
                SqlChangeRequest(
                    change_id="change_1",
                    change_type=SqlChangeType.SEMANTIC_REGENERATION,
                    value=feedback,
                    required=True,
                    deterministic_candidate=False,
                    rationale="El feedback requiere regeneración semántica general.",
                )
            ]
            plan.strategy = SqlFeedbackStrategy.REGENERATE
            plan.requires_regeneration = True
        return self.plan_validator.validate(plan, semantic_context)

    def _merge_deterministic_limit(
        self,
        plan: SqlFeedbackPlan,
        feedback: str,
    ) -> SqlFeedbackPlan:
        requested = self.extract_requested_limit(feedback)
        if requested is None:
            return plan
        for change in plan.changes:
            if change.change_type == SqlChangeType.SET_LIMIT:
                change.limit = requested
                change.deterministic_candidate = True
                return plan
        plan.changes.append(
            SqlChangeRequest(
                change_id=f"change_{len(plan.changes) + 1}",
                change_type=SqlChangeType.SET_LIMIT,
                limit=requested,
                required=True,
                deterministic_candidate=True,
                rationale="Límite numérico solicitado explícitamente por el usuario.",
            )
        )
        plan.strategy = (
            SqlFeedbackStrategy.AST_ONLY
            if len(plan.changes) == 1
            else SqlFeedbackStrategy.HYBRID
        )
        if plan.strategy == SqlFeedbackStrategy.AST_ONLY:
            plan.requires_regeneration = False
        return plan

    @classmethod
    def _deterministic_limit_only_plan(cls, feedback: str | None) -> SqlFeedbackPlan | None:
        if not feedback:
            return None
        for pattern in cls._LIMIT_ONLY_PATTERNS:
            match = pattern.fullmatch(feedback)
            if not match:
                continue
            digits = re.sub(r"[^0-9]", "", match.group(1))
            if not digits:
                return None
            requested = int(digits)
            if requested <= 0:
                return None
            return SqlFeedbackPlan(
                feedback=feedback,
                summary=f"Establecer LIMIT en {requested}",
                strategy=SqlFeedbackStrategy.AST_ONLY,
                changes=[
                    SqlChangeRequest(
                        change_id="change_1",
                        change_type=SqlChangeType.SET_LIMIT,
                        limit=requested,
                        required=True,
                        deterministic_candidate=True,
                        rationale=(
                            "Solicitud completa y no ambigua de cambio de LIMIT; "
                            "no requiere regeneración semántica."
                        ),
                    )
                ],
                requires_regeneration=False,
                confidence=1.0,
            )
        return None

    @classmethod
    def extract_requested_limit(cls, feedback: str | None) -> int | None:
        if not feedback:
            return None
        for pattern in cls._LIMIT_PATTERNS:
            match = pattern.search(feedback)
            if not match:
                continue
            digits = re.sub(r"[^0-9]", "", match.group(1))
            if digits:
                value = int(digits)
                return value if value > 0 else None
        return None
