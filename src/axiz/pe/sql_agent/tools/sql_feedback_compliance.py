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
    SqlTemporalScope,
)


class SqlFeedbackComplianceValidator:
    """Combine deterministic AST postconditions with semantic LLM review."""

    def __init__(self, dialect: str) -> None:
        self.dialect = dialect

    @staticmethod
    def _is_generic_revision(plan: SqlFeedbackPlan) -> bool:
        return bool(
            plan.strategy.value == "regenerate"
            and (plan.raw_user_message or plan.feedback)
            and not plan.changes
        )

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
        deterministic_unexpected = self._baseline_unexpected_changes(
            previous_sql,
            final_sql,
            plan,
        )
        unexpected = list(
            dict.fromkeys(deterministic_unexpected + list(semantic.unexpected_changes))
        )
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
        requested = (
            ["revision"]
            if self._is_generic_revision(plan)
            else [change.change_id for change in plan.changes if change.required]
        )
        compliant = (
            deterministic_compliant
            and semantic.compliant
            and not missing
            and not unexpected
        )
        retry_instruction = None
        if not compliant and not semantic.requires_clarification:
            labels = ", ".join(missing or requested)
            unexpected_text = (
                " Cambios no solicitados detectados: " + "; ".join(unexpected) + "."
                if unexpected
                else ""
            )
            raw_feedback = plan.raw_user_message or plan.feedback
            retry_instruction = (
                "Revisa la sentencia SQL completa y aplica literalmente este feedback del usuario: "
                + repr(raw_feedback)
                + ". Usa el SQL anterior como baseline, conserva toda cláusula no solicitada y "
                f"corrige los cambios todavía incumplidos: {labels}."
                + unexpected_text
            )
        return FeedbackComplianceResult(
            compliant=compliant,
            deterministic_compliant=deterministic_compliant,
            semantic_compliant=semantic.compliant,
            requested_changes=requested,
            applied_changes=applied,
            missing_changes=missing,
            unexpected_changes=unexpected,
            checks=checks,
            confidence=semantic.confidence,
            requires_clarification=semantic.requires_clarification,
            clarification_question=semantic.clarification_question,
            retry_instruction=retry_instruction,
        )

    def _baseline_unexpected_changes(
        self,
        previous_sql: str,
        final_sql: str,
        plan: SqlFeedbackPlan,
    ) -> list[str]:
        if not previous_sql:
            return []
        import sqlglot
        from sqlglot import exp

        try:
            previous = sqlglot.parse_one(previous_sql, read=self.dialect)
            final = sqlglot.parse_one(final_sql, read=self.dialect)
        except sqlglot.errors.ParseError:
            return []

        if self._is_generic_revision(plan):
            return []
        types = {change.change_type for change in plan.changes}
        if SqlChangeType.SEMANTIC_REGENERATION in types:
            return []
        previous_select = previous if isinstance(previous, exp.Select) else previous.find(exp.Select)
        final_select = final if isinstance(final, exp.Select) else final.find(exp.Select)
        if previous_select is None or final_select is None:
            return []

        unexpected: list[str] = []

        if SqlChangeType.SET_LIMIT not in types and self._arg(previous, "limit") != self._arg(final, "limit"):
            unexpected.append("se modificó LIMIT sin solicitarlo")
        if SqlChangeType.CHANGE_ORDER not in types and self._arg(previous_select, "order") != self._arg(final_select, "order"):
            unexpected.append("se modificó ORDER BY sin solicitarlo")

        filter_types = {
            SqlChangeType.ADD_FILTER,
            SqlChangeType.REMOVE_FILTER,
            SqlChangeType.REPLACE_FILTER,
            SqlChangeType.CHANGE_TIME_WINDOW,
        }
        if not (types & filter_types) and self._arg(previous_select, "where") != self._arg(final_select, "where"):
            unexpected.append("se modificaron filtros sin solicitarlo")

        dimension_types = {
            SqlChangeType.ADD_DIMENSION,
            SqlChangeType.REMOVE_DIMENSION,
            SqlChangeType.CHANGE_GROUPING,
        }
        comparative_temporal = [
            change
            for change in plan.changes
            if change.change_type == SqlChangeType.CHANGE_TIME_WINDOW
            and change.time_window_scope != SqlTemporalScope.OVERALL_WINDOW
        ]
        temporal_series_change = any(
            change.time_window_scope == SqlTemporalScope.COMPARISON_SERIES
            for change in comparative_temporal
        )
        if (
            not (types & dimension_types)
            and not temporal_series_change
            and self._arg(previous_select, "group") != self._arg(final_select, "group")
        ):
            unexpected.append("se modificó GROUP BY sin solicitarlo")

        projection_types = dimension_types | {
            SqlChangeType.ADD_METRIC,
            SqlChangeType.REMOVE_METRIC,
            SqlChangeType.REPLACE_METRIC,
        }
        if not (types & projection_types) and not comparative_temporal:
            if self._expressions(previous_select) != self._expressions(final_select):
                unexpected.append("se modificó la proyección de métricas/dimensiones sin solicitarlo")
        if SqlChangeType.REPLACE_SOURCE not in types:
            if self._sources(previous) != self._sources(final):
                unexpected.append("se modificaron las fuentes semánticas sin solicitarlo")
        return unexpected

    def _arg(self, expression: Any, key: str) -> str:
        value = expression.args.get(key)
        if value is None:
            return ""
        if isinstance(value, list):
            return "|".join(
                item.sql(dialect=self.dialect).strip().lower() for item in value
            )
        return value.sql(dialect=self.dialect).strip().lower()

    def _expressions(self, select: Any) -> list[str]:
        return [
            item.sql(dialect=self.dialect).strip().lower()
            for item in (select.args.get("expressions") or [])
        ]

    def _sources(self, statement: Any) -> list[str]:
        from sqlglot import exp

        return sorted(
            table.sql(dialect=self.dialect).strip().lower()
            for table in statement.find_all(exp.Table)
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
            from axiz.pe.sql_agent.tools.sql_feedback import SqlFeedbackApplier
            from axiz.pe.sql_agent.tools.temporal_query_shape import TemporalQueryShapeAnalyzer

            sql_text = tree.sql(dialect=self.dialect)
            if change.time_window_scope != SqlTemporalScope.OVERALL_WINDOW:
                # Comparative baseline/series changes can alter projection and aggregation.
                # A simple interval count is not sufficient proof, so deterministic compliance
                # deliberately delegates them to the independent semantic reviewer.
                shape = TemporalQueryShapeAnalyzer.analyze(sql_text)
                supported = False
                passed = None
                evidence = (
                    f"scope={change.time_window_scope.value}; "
                    f"comparison_periods={change.comparison_periods}; "
                    f"topology={shape.topology.value}; offsets={list(shape.bucket_offsets)}"
                )
            else:
                expected_days = application.applied_time_window_days
                if expected_days is None:
                    expected_days = change.time_window_days
                if expected_days is not None or change.time_window_delta_days is not None:
                    actual_days = SqlFeedbackApplier.rolling_day_window_days(
                        sql_text,
                        dialect=self.dialect,
                    )
                    passed = expected_days is not None and actual_days == expected_days
                    evidence = f"días actuales={actual_days}, solicitados={expected_days}"
                else:
                    actual_months = SqlFeedbackApplier.closed_month_window_months(
                        sql_text,
                        dialect=self.dialect,
                    )
                    expected_months = application.applied_time_window_months
                    if expected_months is None:
                        expected_months = change.time_window_months
                    passed = expected_months is not None and actual_months == expected_months
                    evidence = (
                        f"meses actuales={actual_months}, solicitados={expected_months}"
                    )
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
