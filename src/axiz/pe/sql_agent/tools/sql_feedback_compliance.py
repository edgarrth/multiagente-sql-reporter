from __future__ import annotations

from typing import Any

from axiz.pe.sql_agent.models.contracts import (
    FeedbackComplianceCheck,
    FeedbackComplianceResult,
    FeedbackSemanticComplianceOutput,
    SqlChangeRequest,
    SqlChangeType,
    SqlFeedbackApplication,
    SqlFeedbackPlan,
    SqlGenerationOutput,
)


class SqlFeedbackComplianceValidator:
    """Combine deterministic AST postconditions with semantic LLM review."""

    def __init__(self, dialect: str) -> None:
        self.dialect = dialect

    def validate(
        self,
        *,
        plan: SqlFeedbackPlan,
        previous_sql: str,
        final_sql: str,
        generated: SqlGenerationOutput,
        application: SqlFeedbackApplication,
        semantic: FeedbackSemanticComplianceOutput,
    ) -> FeedbackComplianceResult:
        checks = self._deterministic_checks(plan, final_sql, generated, application)
        deterministic_missing = [
            check.change_id
            for check in checks
            if check.supported_deterministically and check.passed is False
        ]
        deterministic_compliant = not deterministic_missing
        semantic_missing = list(semantic.missing_changes)
        missing = list(dict.fromkeys(deterministic_missing + semantic_missing))
        applied = list(
            dict.fromkeys(
                application.applied_changes
                + semantic.applied_changes
                + [
                    check.change_id
                    for check in checks
                    if check.supported_deterministically and check.passed is True
                ]
            )
        )
        requested = [change.change_id for change in plan.changes if change.required]
        compliant = deterministic_compliant and semantic.compliant and not missing
        retry_instruction = None
        if not compliant and not semantic.requires_clarification:
            labels = ", ".join(missing or requested)
            retry_instruction = (
                "Regenera el SQL aplicando obligatoriamente todos los cambios del plan. "
                f"Cambios todavía incumplidos: {labels}. Conserva métricas, dimensiones, "
                "filtros, periodo y fuentes que el usuario no pidió modificar."
            )
        return FeedbackComplianceResult(
            compliant=compliant,
            deterministic_compliant=deterministic_compliant,
            semantic_compliant=semantic.compliant,
            requested_changes=requested,
            applied_changes=applied,
            missing_changes=missing,
            unexpected_changes=semantic.unexpected_changes,
            checks=checks,
            confidence=semantic.confidence,
            requires_clarification=semantic.requires_clarification,
            clarification_question=semantic.clarification_question,
            retry_instruction=retry_instruction,
        )

    def _deterministic_checks(
        self,
        plan: SqlFeedbackPlan,
        final_sql: str,
        generated: SqlGenerationOutput,
        application: SqlFeedbackApplication,
    ) -> list[FeedbackComplianceCheck]:
        import sqlglot

        try:
            tree = sqlglot.parse_one(final_sql, read=self.dialect)
        except sqlglot.errors.ParseError as exc:
            return [
                FeedbackComplianceCheck(
                    change_id=change.change_id,
                    change_type=change.change_type,
                    supported_deterministically=True,
                    passed=False,
                    evidence=f"SQL no parseable: {exc}",
                )
                for change in plan.changes
            ]
        return [
            self._check_change(tree, change, generated, application)
            for change in plan.changes
        ]

    def _check_change(
        self,
        tree: Any,
        change: SqlChangeRequest,
        generated: SqlGenerationOutput,
        application: SqlFeedbackApplication,
    ) -> FeedbackComplianceCheck:
        from sqlglot import exp

        supported = True
        passed: bool | None = None
        evidence: str | None = None
        target = self._key(change.target or "")
        select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)

        if change.change_type == SqlChangeType.SET_LIMIT:
            limit = tree.args.get("limit")
            expression = limit.args.get("expression") if limit else None
            actual = (
                int(expression.this)
                if isinstance(expression, exp.Literal) and expression.is_int
                else None
            )
            expected = (
                application.applied_limit
                if application.requested_limit == change.limit and application.applied_limit is not None
                else change.limit
            )
            passed = expected is not None and actual == expected
            evidence = f"LIMIT actual={actual}, solicitado={expected}"
        elif change.change_type in {
            SqlChangeType.ADD_FILTER,
            SqlChangeType.REPLACE_FILTER,
        }:
            where = select.args.get("where") if select else None
            text = where.sql(dialect=self.dialect).lower() if where else ""
            value = (change.value or "").strip(" '\"").lower()
            passed = bool(target and target in text and (not value or value in text))
            evidence = text or "No existe WHERE"
        elif change.change_type == SqlChangeType.REMOVE_FILTER:
            where = select.args.get("where") if select else None
            text = where.sql(dialect=self.dialect).lower() if where else ""
            passed = not target or target not in text
            evidence = text or "No existe WHERE"
        elif change.change_type == SqlChangeType.CHANGE_ORDER:
            order = select.args.get("order") if select else None
            text = order.sql(dialect=self.dialect).lower() if order else ""
            requested_direction = str(change.direction or "").split(".")[-1].lower()
            matched = None
            if order:
                for ordered in order.expressions:
                    ordered_text = ordered.this.sql(dialect=self.dialect).lower()
                    if target and target in ordered_text:
                        matched = ordered
                        break
            actual_direction = (
                "desc" if matched is not None and bool(matched.args.get("desc")) else "asc"
            )
            passed = bool(
                matched is not None
                and (not requested_direction or requested_direction == actual_direction)
            )
            evidence = (text or "No existe ORDER BY") + f"; direction={actual_direction}"
        elif change.change_type in {
            SqlChangeType.ADD_DIMENSION,
            SqlChangeType.CHANGE_GROUPING,
        }:
            selected = {self._key(item) for item in generated.selected_dimensions}
            group = select.args.get("group") if select else None
            group_text = group.sql(dialect=self.dialect).lower() if group else ""
            passed = bool(target and (target in selected or target in group_text))
            evidence = f"dimensions={sorted(selected)}; group_by={group_text}"
        elif change.change_type == SqlChangeType.REMOVE_DIMENSION:
            selected = {self._key(item) for item in generated.selected_dimensions}
            passed = not target or target not in selected
            evidence = f"dimensions={sorted(selected)}"
        elif change.change_type in {
            SqlChangeType.ADD_METRIC,
            SqlChangeType.REPLACE_METRIC,
        }:
            metrics = {self._key(item) for item in generated.selected_metrics}
            passed = bool(target and target in metrics)
            evidence = f"metrics={sorted(metrics)}"
        elif change.change_type == SqlChangeType.REMOVE_METRIC:
            metrics = {self._key(item) for item in generated.selected_metrics}
            passed = not target or target not in metrics
            evidence = f"metrics={sorted(metrics)}"
        elif change.change_type == SqlChangeType.REPLACE_SOURCE:
            tables = {self._key(table.name) for table in tree.find_all(exp.Table)}
            passed = bool(target and target in tables)
            evidence = f"sources={sorted(tables)}"
        elif change.change_type == SqlChangeType.CHANGE_TIME_WINDOW:
            window = generated.time_window
            passed = window is not None and bool(
                window.start_expression or window.end_expression or window.label
            )
            evidence = window.model_dump_json() if window else "No se declaró time_window"
        else:
            supported = False
            passed = None
            evidence = "Requiere validación semántica"

        return FeedbackComplianceCheck(
            change_id=change.change_id,
            change_type=change.change_type,
            supported_deterministically=supported,
            passed=passed,
            evidence=evidence,
        )

    @staticmethod
    def _key(value: str) -> str:
        return value.split(".")[-1].strip('"').lower()
