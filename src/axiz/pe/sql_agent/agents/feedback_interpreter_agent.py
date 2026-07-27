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
from axiz.pe.sql_agent.tools.sql_feedback import SqlFeedbackApplier
from axiz.pe.sql_agent.tools.sql_feedback_plan import SqlFeedbackPlanValidator


class FeedbackInterpreterAgent:
    """Translate free-form HITL feedback into a governed semantic change plan.

    Low-risk revisions that can be proven against the previous SQL contract are parsed locally.
    Everything else is delegated to the configured LLM and still validated against the catalog.
    """

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

    _LIMIT_CLAUSE_PATTERNS = (
        re.compile(
            r"(?ix)\b(?:pon(?:le)?|agrega|añade|establece|define|aplica|cambia|ajusta|"
            r"modifica|reduce|baja|sube|aumenta|incrementa)\s+"
            r"(?:un|el)?\s*l[ií]mite"
            r"(?:\s+de\s+(?:filas|registros|resultados))?\s*"
            r"(?:de|a|en|=|:)?\s*(\d[\d._,]*)\s*"
            r"(?:filas|registros|resultados)?"
            r"(?:\s+(?:a|en|para)\s+(?:la\s+)?(?:query|consulta|sql))?"
        ),
        re.compile(
            r"(?ix)\b(?:l[ií]mite|limit|top|max(?:imo)?|m[aá]ximo)\b"
            r"\s*(?:de|a|en|=|:)?\s*(\d[\d._,]*)\s*"
            r"(?:filas|registros|resultados)?"
            r"(?:\s+(?:a|en|para)\s+(?:la\s+)?(?:query|consulta|sql))?"
        ),
        re.compile(
            r"(?ix)\b(?:muestra|devuelve|retorna|trae|obt[eé]n)\s+"
            r"(?:como\s+m[aá]ximo\s+)?(\d[\d._,]*)\s+"
            r"(?:filas|registros|resultados)\b"
        ),
    )

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

    # Only relative changes to a previously certified closed-month window are eligible.
    # The SQL AST is inspected before this fast path is selected, so a rolling/daily window is
    # never changed merely because the text mentions "mes".
    _MONTH_DELTA_PATTERNS = (
        (
            -1,
            re.compile(
                r"(?ix)\b(?:reduce|disminuye|acorta|baja|resta|quita)"
                r"\s+(?:el\s+periodo\s+)?(?:en\s+)?"
                r"(\d+|un|una)\s+mes(?:es)?"
                r"(?:\s+(?:de\s+la|del?|a\s+la|en\s+la)\s+(?:query|consulta|sql))?\b"
            ),
        ),
        (
            1,
            re.compile(
                r"(?ix)\b(?:aumenta|incrementa|ampl[ií]a|extiende|suma|agrega|añade)"
                r"\s+(?:el\s+periodo\s+)?(?:en\s+)?"
                r"(\d+|un|una)\s+mes(?:es)?"
                r"(?:\s+(?:a\s+la|al?|en\s+la|de\s+la)\s+(?:query|consulta|sql))?\b"
            ),
        ),
    )

    _DAY_DELTA_PATTERNS = (
        (
            -1,
            re.compile(
                r"(?ix)\b(?:reduce|disminuye|acorta|baja|resta|quita)"
                r"\s+(?:el\s+(?:periodo|rango|ventana|alcance)\s+)?(?:en\s+)?"
                r"(\d+|un|una)\s+d[ií]a(?:s)?"
                r"(?:\s+(?:de\s+la|del?|a\s+la|en\s+la)\s+"
                r"(?:b[uú]squeda|query|consulta|sql|ventana|periodo|rango)"
                r"(?:\s+de\s+[\wáéíóúñ-]+(?:\s+[\wáéíóúñ-]+){0,3})?)?\b"
            ),
        ),
        (
            1,
            re.compile(
                r"(?ix)\b(?:aumenta|incrementa|ampl[ií]a|extiende|suma|agrega(?:le)?|añade)"
                r"\s+(?:el\s+(?:periodo|rango|ventana|alcance)\s+)?(?:en\s+)?"
                r"(\d+|un|una)\s+d[ií]a(?:s)?"
                r"(?:\s+(?:a\s+la|al?|en\s+la|de\s+la)\s+"
                r"(?:b[uú]squeda|query|consulta|sql|ventana|periodo|rango)"
                r"(?:\s+de\s+[\wáéíóúñ-]+(?:\s+[\wáéíóúñ-]+){0,3})?)?\b"
            ),
        ),
    )

    # Elliptical follow-ups such as "agrégale 15 a la búsqueda de liquidaciones" are only
    # resolved locally when the previous SQL exposes exactly one governed time-window unit.
    # The unit is inherited from that verified AST; it is never guessed from domain words.
    _UNIT_LESS_WINDOW_DELTA_PATTERNS = (
        (
            -1,
            re.compile(
                r"(?ix)^\s*(?:reduce|disminuye|acorta|baja|resta|quita)"
                r"\s+(\d+|un|una)\s+(?:a\s+la|al?|de\s+la|en\s+la)\s+"
                r"(?:b[uú]squeda|ventana|periodo|rango|consulta|query|sql)"
                r"(?:\s+de\s+[\wáéíóúñ-]+(?:\s+[\wáéíóúñ-]+){0,3})?\s*[.!]?\s*$"
            ),
        ),
        (
            1,
            re.compile(
                r"(?ix)^\s*(?:aumenta|incrementa|ampl[ií]a|extiende|suma|agrega(?:le)?|añade)"
                r"\s+(\d+|un|una)\s+(?:a\s+la|al?|de\s+la|en\s+la)\s+"
                r"(?:b[uú]squeda|ventana|periodo|rango|consulta|query|sql)"
                r"(?:\s+de\s+[\wáéíóúñ-]+(?:\s+[\wáéíóúñ-]+){0,3})?\s*[.!]?\s*$"
            ),
        ),
    )

    _CONNECTOR_ONLY = re.compile(
        r"(?ix)^(?:\s|[,;:.]|\b(?:y|e|adem[aá]s|tambi[eé]n|por\s+favor)\b)*$"
    )

    def __init__(
        self,
        llm: StructuredLLM,
        max_result_rows: int,
        plan_validator: SqlFeedbackPlanValidator,
        dialect: str = "postgres",
    ) -> None:
        self.llm = llm
        self.max_result_rows = max_result_rows
        self.plan_validator = plan_validator
        self.dialect = dialect

    async def interpret(
        self,
        *,
        feedback: str,
        previous_sql: str,
        semantic_context: dict[str, Any],
        current_contract: dict[str, Any],
    ) -> SqlFeedbackPlan:
        deterministic = self._deterministic_structural_plan(
            feedback,
            previous_sql=previous_sql,
            dialect=self.dialect,
        )
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
- A closed-month window adjustment may be deterministic only when the previous SQL exposes a
  single certified month interval; otherwise it requires regeneration.
- Metrics, dimensions, grouping, source selection, complex periods and business-meaning changes
  require regeneration, even if a later AST step can enforce part of the request.
- Preserve every aspect of the previous analytical contract not explicitly changed.
- Use requires_clarification only when the request cannot be resolved from the previous contract
  and semantic catalog. Do not ask for clarification merely because the feedback is concise.
- For filters, return target, operator and value/values. Use canonical SQL operators such as =,
  !=, >, >=, <, <=, IN, NOT IN, LIKE, ILIKE, IS NULL and IS NOT NULL.
- For ordering, return target and asc/desc.
- For LIMIT, return the exact requested integer. The platform will apply its governed maximum.
- For a relative month adjustment, use time_window_delta_months. For an absolute month count, use
  time_window_months. For rolling-day windows, use time_window_delta_days or time_window_days.
  Never mix month and day fields in the same change.
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
    def _deterministic_structural_plan(
        cls,
        feedback: str | None,
        *,
        previous_sql: str,
        dialect: str = "postgres",
    ) -> SqlFeedbackPlan | None:
        """Return an AST-only plan only when the complete message is safely understood."""
        if not feedback:
            return None

        limit_match = cls._find_limit_match(feedback)
        month_match = cls._find_month_delta_match(feedback)
        day_match = cls._find_day_delta_match(feedback)
        inferred_match = None
        if month_match is None and day_match is None:
            inferred_match = cls._find_unit_less_window_delta_match(
                feedback,
                previous_sql=previous_sql,
                dialect=dialect,
            )
        if month_match is not None and day_match is not None:
            return None
        spans: list[tuple[int, int]] = []
        changes: list[SqlChangeRequest] = []

        if limit_match is not None:
            match, requested = limit_match
            spans.append(match.span())
            changes.append(
                SqlChangeRequest(
                    change_id=f"change_{len(changes) + 1}",
                    change_type=SqlChangeType.SET_LIMIT,
                    limit=requested,
                    required=True,
                    deterministic_candidate=True,
                    rationale="Límite numérico solicitado explícitamente por el usuario.",
                )
            )

        if month_match is not None:
            match, delta = month_match
            current_months = SqlFeedbackApplier.closed_month_window_months(
                previous_sql,
                dialect=dialect,
            )
            if current_months is None or current_months + delta < 1:
                return None
            spans.append(match.span())
            changes.append(
                SqlChangeRequest(
                    change_id=f"change_{len(changes) + 1}",
                    change_type=SqlChangeType.CHANGE_TIME_WINDOW,
                    time_window_delta_months=delta,
                    required=True,
                    deterministic_candidate=True,
                    rationale=(
                        "Ajuste relativo sobre una única ventana mensual cerrada verificada "
                        "en el SQL previamente aprobado."
                    ),
                )
            )

        if day_match is not None:
            match, delta = day_match
            current_days = SqlFeedbackApplier.rolling_day_window_days(
                previous_sql,
                dialect=dialect,
            )
            if current_days is None or current_days + delta < 1:
                return None
            spans.append(match.span())
            changes.append(
                SqlChangeRequest(
                    change_id=f"change_{len(changes) + 1}",
                    change_type=SqlChangeType.CHANGE_TIME_WINDOW,
                    time_window_delta_days=delta,
                    required=True,
                    deterministic_candidate=True,
                    rationale=(
                        "Ajuste relativo sobre una única ventana diaria cerrada verificada "
                        "en el SQL previamente aprobado."
                    ),
                )
            )

        if inferred_match is not None:
            match, delta, unit = inferred_match
            spans.append(match.span())
            kwargs: dict[str, int] = (
                {"time_window_delta_days": delta}
                if unit == "day"
                else {"time_window_delta_months": delta}
            )
            changes.append(
                SqlChangeRequest(
                    change_id=f"change_{len(changes) + 1}",
                    change_type=SqlChangeType.CHANGE_TIME_WINDOW,
                    required=True,
                    deterministic_candidate=True,
                    rationale=(
                        "El mensaje omite la unidad, pero el SQL anterior contiene una única "
                        f"ventana gobernada en {unit}s; se conserva esa unidad."
                    ),
                    **kwargs,
                )
            )

        if not changes or not cls._only_recognized_spans(feedback, spans):
            return None

        summary_parts = []
        for change in changes:
            if change.change_type == SqlChangeType.SET_LIMIT:
                summary_parts.append(f"establecer LIMIT en {change.limit}")
            elif change.time_window_delta_months:
                verb = "ampliar" if change.time_window_delta_months > 0 else "reducir"
                summary_parts.append(
                    f"{verb} la ventana cerrada en {abs(change.time_window_delta_months)} mes(es)"
                )
            elif change.time_window_delta_days:
                verb = "ampliar" if change.time_window_delta_days > 0 else "reducir"
                summary_parts.append(
                    f"{verb} la ventana cerrada en {abs(change.time_window_delta_days)} día(s)"
                )
        return SqlFeedbackPlan(
            feedback=feedback,
            summary="; ".join(summary_parts),
            strategy=SqlFeedbackStrategy.AST_ONLY,
            changes=changes,
            requires_regeneration=False,
            confidence=1.0,
        )

    @classmethod
    def _deterministic_limit_only_plan(cls, feedback: str | None) -> SqlFeedbackPlan | None:
        """Compatibility helper used by older tests and integrations."""
        if not feedback:
            return None
        for pattern in cls._LIMIT_ONLY_PATTERNS:
            match = pattern.fullmatch(feedback)
            if not match:
                continue
            requested = cls._parse_positive_int(match.group(1))
            if requested is None:
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
    def _find_limit_match(cls, feedback: str) -> tuple[re.Match[str], int] | None:
        for pattern in (*cls._LIMIT_CLAUSE_PATTERNS, *cls._LIMIT_PATTERNS):
            match = pattern.search(feedback)
            if not match:
                continue
            requested = cls._parse_positive_int(match.group(1))
            if requested is not None:
                return match, requested
        return None

    @classmethod
    def _find_month_delta_match(cls, feedback: str) -> tuple[re.Match[str], int] | None:
        for direction, pattern in cls._MONTH_DELTA_PATTERNS:
            match = pattern.search(feedback)
            if not match:
                continue
            raw = match.group(1).lower()
            amount = 1 if raw in {"un", "una"} else int(raw)
            if amount <= 0:
                return None
            return match, direction * amount
        return None

    @classmethod
    def _find_day_delta_match(cls, feedback: str) -> tuple[re.Match[str], int] | None:
        for direction, pattern in cls._DAY_DELTA_PATTERNS:
            match = pattern.search(feedback)
            if not match:
                continue
            raw = match.group(1).lower()
            amount = 1 if raw in {"un", "una"} else int(raw)
            if amount <= 0:
                return None
            return match, direction * amount
        return None

    @classmethod
    def _find_unit_less_window_delta_match(
        cls,
        feedback: str,
        *,
        previous_sql: str,
        dialect: str,
    ) -> tuple[re.Match[str], int, str] | None:
        if re.search(r"(?ix)\b(?:l[ií]mite|filas|registros|resultados|top)\b", feedback):
            return None
        day_window = SqlFeedbackApplier.rolling_day_window_days(
            previous_sql,
            dialect=dialect,
        )
        month_window = SqlFeedbackApplier.closed_month_window_months(
            previous_sql,
            dialect=dialect,
        )
        if (day_window is None) == (month_window is None):
            return None
        for direction, pattern in cls._UNIT_LESS_WINDOW_DELTA_PATTERNS:
            match = pattern.fullmatch(feedback)
            if not match:
                continue
            raw = match.group(1).lower()
            amount = 1 if raw in {"un", "una"} else int(raw)
            if amount <= 0:
                return None
            delta = direction * amount
            current = day_window if day_window is not None else month_window
            if current is None or current + delta < 1:
                return None
            return match, delta, "day" if day_window is not None else "month"
        return None

    @classmethod
    def _only_recognized_spans(
        cls,
        feedback: str,
        spans: list[tuple[int, int]],
    ) -> bool:
        chars = list(feedback)
        for start, end in sorted(spans, reverse=True):
            chars[start:end] = " " * (end - start)
        remainder = "".join(chars)
        return bool(cls._CONNECTOR_ONLY.fullmatch(remainder))

    @classmethod
    def extract_requested_limit(cls, feedback: str | None) -> int | None:
        if not feedback:
            return None
        found = cls._find_limit_match(feedback)
        return found[1] if found else None

    @staticmethod
    def _parse_positive_int(value: str) -> int | None:
        digits = re.sub(r"[^0-9]", "", value)
        parsed = int(digits) if digits else 0
        return parsed if parsed > 0 else None
