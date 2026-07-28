from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from axiz.pe.sql_agent.models.query_spec import (
    CompiledSqlArtifact,
    FilterBooleanOperator,
    QuerySpecPatch,
    QuerySpecPatchOperation,
    QuerySpecResolution,
    SemanticDimension,
    SemanticFilterGroup,
    SemanticMeasure,
    SemanticOrder,
    SemanticPredicate,
    SemanticQuerySpec,
    SemanticTimeFilter,
    SemanticTimeRange,
)


class SemanticQuerySpecService:
    """Build, version and resolve canonical semantic query specifications.

    The specification is the analytical source of truth. SQL remains the executable artifact. Agents
    exchange a reference plus a semantic patch while the complete specification is retained in
    state.
    """

    def __init__(self, *, dialect: str = "postgres") -> None:
        self.dialect = dialect

    def from_contract(
        self,
        contract: dict[str, Any] | None,
        *,
        previous_sql: str = "",
        original_question: str = "",
        raw_user_message: str = "",
    ) -> SemanticQuerySpec:
        payload = dict(contract or {})
        existing = payload.get("query_spec") or payload.get("semantic_query_spec")
        if existing:
            spec = SemanticQuerySpec.model_validate(existing)
            spec = self._normalize_spec(spec, sql=previous_sql)
            return spec.model_copy(
                update={
                    "raw_user_message": raw_user_message or spec.raw_user_message,
                    "original_question": original_question or spec.original_question,
                }
            )

        sources = list(payload.get("sources") or payload.get("source_objects") or [])
        spec_id = str(payload.get("query_spec_id") or self._spec_id(original_question, sources))
        measures = [
            SemanticMeasure(member=str(item), alias=self._identifier(str(item)))
            for item in (payload.get("metrics") or payload.get("selected_metrics") or [])
            if item
        ]
        dimensions = [
            SemanticDimension(member=str(item), alias=self._identifier(str(item)))
            for item in (payload.get("dimensions") or payload.get("selected_dimensions") or [])
            if item
        ]
        time_filters = self._time_filters(payload.get("time_window"), previous_sql)
        filters = self._filters(
            payload.get("filters") or payload.get("selected_filters") or [],
            excluded_time_members=[item.member for item in time_filters],
        )
        order_by = self._orders(payload.get("ordering") or [], previous_sql)
        limit = payload.get("limit")
        if limit is None:
            limit = self._limit(previous_sql)
        projection_aliases = self._projection_aliases(previous_sql)
        if not measures and projection_aliases:
            dimension_aliases = {item.alias for item in dimensions if item.alias}
            for alias in projection_aliases:
                if alias not in dimension_aliases:
                    measures.append(SemanticMeasure(member=alias, alias=alias))

        return SemanticQuerySpec(
            spec_id=spec_id,
            version=int(payload.get("query_spec_version") or 1),
            semantic_model=sources[0] if len(sources) == 1 else None,
            original_question=original_question,
            raw_user_message=raw_user_message,
            interpretation=str(payload.get("interpretation") or ""),
            measures=measures,
            dimensions=dimensions,
            filters=filters,
            time_filters=time_filters,
            order_by=order_by,
            limit=int(limit) if isinstance(limit, int) or str(limit or "").isdigit() else None,
            source_objects=sources,
            assumptions=list(payload.get("assumptions") or []),
        )

    def from_sql_snapshot(
        self,
        sql: str,
        *,
        base: SemanticQuerySpec | None = None,
        original_question: str = "",
        raw_user_message: str = "",
        interpretation: str = "",
        selected_filters: list[dict[str, Any]] | None = None,
        time_window: dict[str, Any] | None = None,
        assumptions: list[str] | None = None,
    ) -> SemanticQuerySpec:
        """Build a generic semantic snapshot from the complete revised SQL AST.

        This method is used after open-ended feedback. It deliberately derives projection, source,
        ordering and limit from the SQL itself instead of trusting stale fixed properties from the
        previous query specification. The snapshot remains useful for memory and audit while the
        compiled SQL is the authoritative editable artifact for the revision.
        """
        try:
            import sqlglot
            from sqlglot import exp

            tree = sqlglot.parse_one(sql, read=self.dialect)
            root = tree if isinstance(tree, exp.Select) else next(tree.find_all(exp.Select), None)
        except Exception:
            root = None
            tree = None

        measures: list[SemanticMeasure] = []
        dimensions: list[SemanticDimension] = []
        if root is not None:
            for index, projection in enumerate(root.expressions, start=1):
                if isinstance(projection, exp.Star) or projection.find(exp.Star):
                    continue
                alias = projection.alias or None
                columns = list(projection.find_all(exp.Column))
                is_measure = bool(next(projection.find_all(exp.AggFunc), None))
                if isinstance(projection, exp.Column):
                    member = projection.sql(dialect=self.dialect)
                elif len(columns) == 1 and not is_measure and alias:
                    member = columns[0].sql(dialect=self.dialect)
                else:
                    member = projection.this.sql(dialect=self.dialect) if alias else projection.sql(dialect=self.dialect)
                output_alias = alias or (projection.name if isinstance(projection, exp.Column) else None)
                output_alias = output_alias or f"expression_{index}"
                if is_measure:
                    measures.append(
                        SemanticMeasure(member=member, alias=output_alias, aggregation="expression")
                    )
                else:
                    dimensions.append(SemanticDimension(member=member, alias=output_alias))

        sources: list[str] = []
        if tree is not None:
            cte_names = {
                self._identifier(cte.alias_or_name)
                for cte in tree.find_all(exp.CTE)
                if cte.alias_or_name
            }
            sources = sorted(
                {
                    ".".join(str(value) for value in (table.catalog, table.db, table.name) if value)
                    for table in tree.find_all(exp.Table)
                    if self._identifier(table.name) not in cte_names
                }
            )
        time_filters = self._time_filters(time_window, sql)
        filters = self._filters(
            selected_filters or [],
            excluded_time_members=[item.member for item in time_filters],
        )
        spec_id = base.spec_id if base is not None else self._spec_id(original_question, sources)
        version = base.version + 1 if base is not None else 1
        return SemanticQuerySpec(
            spec_id=spec_id,
            version=version,
            semantic_model=sources[0] if len(sources) == 1 else None,
            original_question=original_question or (base.original_question if base else ""),
            raw_user_message=raw_user_message,
            interpretation=interpretation,
            measures=measures,
            dimensions=dimensions,
            filters=filters,
            time_filters=time_filters,
            order_by=self._orders_from_sql(sql),
            limit=self._limit(sql),
            source_objects=sources,
            assumptions=list(assumptions or []),
        )

    def patch_from_intents(
        self,
        base: SemanticQuerySpec,
        *,
        raw_user_message: str,
        intents: Iterable[Any],
        preserve: list[str] | None = None,
    ) -> QuerySpecPatch:
        operations: list[QuerySpecPatchOperation] = []
        for index, intent in enumerate(intents, start=1):
            operation = self._enum_value(getattr(intent, "operation", None)) or "regenerate"
            target = self._enum_value(getattr(intent, "target", None)) or "other"
            field = getattr(intent, "field", None)
            value = getattr(intent, "value", None)
            values = list(getattr(intent, "values", None) or [])
            unit = self._enum_value(getattr(intent, "unit", None))
            if unit == "none":
                unit = None
            direction = None
            candidate_direction = str(value or getattr(intent, "representation", "") or "").lower()
            if candidate_direction in {"asc", "ascending", "ascendente"}:
                direction = "asc"
            elif candidate_direction in {"desc", "descending", "descendente"}:
                direction = "desc"
            member = field
            from_member = None
            to_member = None
            if target in {"metric", "dimension", "source"}:
                to_member = field or (str(value) if value is not None else None)
            elif target == "filter":
                member = field
            elif target == "order":
                member = field
            operations.append(
                QuerySpecPatchOperation(
                    change_id=str(getattr(intent, "change_id", None) or f"change_{index}"),
                    operation=operation,
                    target=target,
                    member=member,
                    from_member=from_member,
                    to_member=to_member,
                    value=value,
                    values=values,
                    unit=unit,
                    scope=str(getattr(intent, "scope", None) or "overall"),
                    direction=direction,
                    predicate_operator=(
                        str(getattr(intent, "operator", None))
                        if getattr(intent, "operator", None)
                        else None
                    ),
                    reason=str(getattr(intent, "rationale", None) or ""),
                )
            )
        return QuerySpecPatch(
            base=base.reference,
            raw_user_message=raw_user_message,
            operations=operations,
            preserve=list(preserve or []),
        )

    def patch_from_changes(
        self,
        base: SemanticQuerySpec,
        *,
        raw_user_message: str,
        changes: Iterable[Any],
        preserve: list[str] | None = None,
    ) -> QuerySpecPatch:
        operations: list[QuerySpecPatchOperation] = []
        for index, change in enumerate(changes, start=1):
            change_type = (
                self._enum_value(getattr(change, "change_type", None))
                or "semantic_regeneration"
            )
            target = "other"
            operation = "regenerate"
            value: Any = getattr(change, "value", None)
            unit: str | None = None
            direction = self._enum_value(getattr(change, "direction", None))
            mapping = {
                "set_limit": ("set", "limit"),
                "add_filter": ("add", "filter"),
                "remove_filter": ("remove", "filter"),
                "replace_filter": ("replace", "filter"),
                "add_dimension": ("add", "dimension"),
                "remove_dimension": ("remove", "dimension"),
                "change_grouping": ("replace", "grouping"),
                "change_order": ("reorder", "order"),
                "add_metric": ("add", "metric"),
                "remove_metric": ("remove", "metric"),
                "replace_metric": ("replace", "metric"),
                "replace_source": ("replace", "source"),
                "semantic_regeneration": ("regenerate", "other"),
            }
            if change_type == "change_time_window":
                target = "time_window"
                if getattr(change, "time_window_days", None) is not None:
                    operation, value, unit = "set", change.time_window_days, "days"
                elif getattr(change, "time_window_delta_days", None) is not None:
                    delta = int(change.time_window_delta_days)
                    operation, value, unit = (
                        "increase" if delta >= 0 else "decrease",
                        abs(delta),
                        "days",
                    )
                elif getattr(change, "time_window_months", None) is not None:
                    operation, value, unit = "set", change.time_window_months, "months"
                elif getattr(change, "time_window_delta_months", None) is not None:
                    delta = int(change.time_window_delta_months)
                    operation, value, unit = (
                        "increase" if delta >= 0 else "decrease",
                        abs(delta),
                        "months",
                    )
                elif getattr(change, "comparison_periods", None) is not None:
                    operation, value, unit = "set", change.comparison_periods, "months"
            else:
                operation, target = mapping.get(change_type, ("regenerate", "other"))
                if change_type == "set_limit":
                    value = getattr(change, "limit", None) or value
            member = getattr(change, "target", None)
            operations.append(
                QuerySpecPatchOperation(
                    change_id=str(getattr(change, "change_id", None) or f"change_{index}"),
                    operation=operation,
                    target=target,
                    member=member,
                    from_member=getattr(change, "previous_target", None),
                    to_member=(
                        member
                        if target in {"metric", "dimension", "source"}
                        and operation in {"add", "replace", "set"}
                        else None
                    ),
                    value=value,
                    values=list(getattr(change, "values", None) or []),
                    unit=unit,
                    scope=self._enum_value(getattr(change, "time_window_scope", None)) or "overall",
                    direction=direction if direction in {"asc", "desc"} else None,
                    predicate_operator=getattr(change, "operator", None),
                    reason=str(getattr(change, "rationale", None) or ""),
                )
            )
        return QuerySpecPatch(
            base=base.reference,
            raw_user_message=raw_user_message,
            operations=operations,
            preserve=list(preserve or []),
        )

    def apply_patch(
        self,
        base: SemanticQuerySpec,
        patch: QuerySpecPatch,
    ) -> QuerySpecResolution:
        if patch.base.id != base.spec_id or patch.base.version != base.version:
            raise ValueError(
                "QuerySpecPatch base reference does not match the current specification"
            )
        resolved = base.model_copy(deep=True)
        resolved.raw_user_message = patch.raw_user_message
        derived: list[QuerySpecPatchOperation] = []
        removed_measure_aliases: set[str] = set()
        replacement_measure_alias: str | None = None

        for operation in patch.operations:
            if operation.target == "limit":
                resolved.limit = self._apply_numeric(
                    resolved.limit,
                    operation.operation,
                    operation.value,
                    minimum=1,
                )
            elif operation.target == "time_window":
                self._apply_time_window(resolved, operation)
            elif operation.target == "filter":
                self._apply_filter(resolved, operation)
            elif operation.target == "metric":
                removed, replacement = self._apply_measure(resolved, operation)
                removed_measure_aliases.update(removed)
                replacement_measure_alias = replacement or replacement_measure_alias
            elif operation.target in {"dimension", "grouping"}:
                self._apply_dimension(resolved, operation)
            elif operation.target == "order":
                self._apply_order(resolved, operation)
            elif operation.target == "source":
                self._apply_source(resolved, operation)

        if removed_measure_aliases:
            reconciled_orders: list[SemanticOrder] = []
            for index, order in enumerate(resolved.order_by):
                if self._identifier(order.member) not in removed_measure_aliases:
                    reconciled_orders.append(order)
                    continue
                if replacement_measure_alias:
                    reconciled_orders.append(
                        SemanticOrder(
                            member=replacement_measure_alias,
                            direction=order.direction,
                        )
                    )
                    derived.append(
                        QuerySpecPatchOperation(
                            change_id=f"derived_order_{index + 1}",
                            operation="replace",
                            target="order",
                            from_member=order.member,
                            to_member=replacement_measure_alias,
                            member=replacement_measure_alias,
                            direction=order.direction,
                            reason=(
                                "The previous ordering depended on a replaced analytical measure."
                            ),
                            derived=True,
                        )
                    )
                else:
                    derived.append(
                        QuerySpecPatchOperation(
                            change_id=f"derived_order_{index + 1}",
                            operation="remove",
                            target="order",
                            from_member=order.member,
                            reason="The previous ordering referenced a removed analytical measure.",
                            derived=True,
                        )
                    )
            resolved.order_by = reconciled_orders

        resolved.version = base.version + 1
        return QuerySpecResolution(
            base=base.reference,
            resolved=resolved,
            requested_patch=patch,
            derived_changes=derived,
        )

    def compile_artifact(
        self,
        spec: SemanticQuerySpec,
        sql: str,
        *,
        source_contracts: dict[str, Any] | None = None,
        execution_state: str = "candidate",
    ) -> CompiledSqlArtifact:
        validation = self.validate_compiled_sql(
            sql,
            source_contracts=source_contracts or {},
            spec=spec,
        )
        return CompiledSqlArtifact(
            query_spec_ref=spec.reference,
            dialect=self.dialect,
            sql=sql,
            sql_hash="sha256:" + hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            validation=validation,
            execution_state=execution_state,
        )

    def validate_compiled_sql(
        self,
        sql: str,
        *,
        source_contracts: dict[str, Any],
        spec: SemanticQuerySpec | None = None,
    ) -> dict[str, Any]:
        try:
            import sqlglot
            from sqlglot import exp

            tree = sqlglot.parse_one(sql, read=self.dialect)
        except Exception as exc:
            return {
                "parse_valid": False,
                "order_dependencies_valid": False,
                "violations": [f"SQL parse error: {exc}"],
            }

        root = tree if isinstance(tree, exp.Select) else next(tree.find_all(exp.Select), None)
        if root is None:
            return {
                "parse_valid": True,
                "order_dependencies_valid": False,
                "violations": ["The SQL does not contain a SELECT projection"],
            }
        aliases = {
            self._identifier(projection.alias)
            for projection in root.expressions
            if projection.alias
        }
        projected_columns = {
            self._identifier(column.name)
            for projection in root.expressions
            for column in projection.find_all(exp.Column)
            if column.name
        }
        allowed_columns = {
            self._identifier(str(column))
            for contract in source_contracts.values()
            for column in (contract or {}).get("columns", [])
        }
        order = root.args.get("order")
        invalid: list[str] = []
        if order is not None:
            for ordered in order.expressions:
                expression = ordered.this if isinstance(ordered, exp.Ordered) else ordered
                for column in expression.find_all(exp.Column):
                    name = self._identifier(column.name)
                    if name in aliases or name in projected_columns or name in allowed_columns:
                        continue
                    invalid.append(column.name)

        violations: list[str] = []
        if invalid:
            violations.append(
                "ORDER BY references unavailable members: "
                + ", ".join(sorted(set(invalid)))
            )

        spec_violations: list[str] = []
        if spec is not None:
            projected_identifiers = aliases | projected_columns
            missing_projection: list[str] = []
            for item in [*spec.measures, *spec.dimensions]:
                candidates = self._member_identifiers(item.member, item.alias)
                if not (candidates & projected_identifiers):
                    missing_projection.append(item.alias or item.member)
            if missing_projection:
                spec_violations.append(
                    "Compiled SQL is missing query-spec projection members: "
                    + ", ".join(sorted(missing_projection))
                )

            actual_sources = {
                ".".join(
                    str(value)
                    for value in (table.catalog, table.db, table.name)
                    if value
                )
                for table in tree.find_all(exp.Table)
            }
            expected_sources = set(spec.source_objects)
            missing_sources = sorted(expected_sources - actual_sources)
            if missing_sources:
                spec_violations.append(
                    "Compiled SQL is missing query-spec sources: "
                    + ", ".join(missing_sources)
                )

            order_sql = order.sql(dialect=self.dialect).lower() if order is not None else ""
            normalized_order_sql = self._identifier(order_sql)
            missing_orders = [
                item.member
                for item in spec.order_by
                if not any(
                    candidate in normalized_order_sql
                    for candidate in self._member_identifiers(item.member)
                )
            ]
            if missing_orders:
                spec_violations.append(
                    "Compiled SQL is missing query-spec ordering: "
                    + ", ".join(missing_orders)
                )

            limit_node = tree.args.get("limit")
            limit_expression = (
                limit_node.args.get("expression") if limit_node is not None else None
            )
            actual_limit = (
                int(limit_expression.this)
                if isinstance(limit_expression, exp.Literal) and limit_expression.is_int
                else None
            )
            if spec.limit is not None and actual_limit != spec.limit:
                spec_violations.append(
                    f"Compiled SQL LIMIT {actual_limit!r} does not match query spec {spec.limit}"
                )

            where_sql = " ".join(
                where.sql(dialect=self.dialect).lower()
                for where in tree.find_all(exp.Where)
            )
            normalized_where_sql = self._identifier(where_sql)
            for time_filter in spec.time_filters:
                if time_filter.member != "__default_time__" and not any(
                    candidate in normalized_where_sql
                    for candidate in self._member_identifiers(time_filter.member)
                ):
                    spec_violations.append(
                        "Compiled SQL is missing query-spec time member: "
                        + time_filter.member
                    )
                    continue
                expected_periods = time_filter.range.value or time_filter.range.periods
                if expected_periods is not None and str(expected_periods) not in where_sql:
                    spec_violations.append(
                        "Compiled SQL does not contain the query-spec time quantity "
                        f"{expected_periods} for {time_filter.member}"
                    )

            for predicate in self._predicates(spec.filters):
                if not any(
                    candidate in normalized_where_sql
                    for candidate in self._member_identifiers(predicate.member)
                ):
                    spec_violations.append(
                        f"Compiled SQL is missing query-spec filter member: {predicate.member}"
                    )
                    continue
                for value in predicate.values:
                    if not self._where_contains_value(where_sql, value):
                        spec_violations.append(
                            "Compiled SQL is missing query-spec filter value "
                            f"{value!r} for {predicate.member}"
                        )

        violations.extend(spec_violations)
        return {
            "parse_valid": True,
            "order_dependencies_valid": not invalid,
            "query_spec_alignment_valid": not spec_violations,
            "projection_aliases": sorted(aliases),
            "invalid_order_references": sorted(set(invalid)),
            "query_spec_violations": spec_violations,
            "violations": violations,
        }

    def _where_contains_value(self, where_sql: str, value: Any) -> bool:
        """Compare literal or expression values without depending on SQL rendering style."""
        raw = str(value).strip()
        if not raw:
            return True
        if raw.lower() in where_sql:
            return True
        actual_key = re.sub(r"\s+", "", where_sql.lower())
        expected_key = self._canonical_expression_key(raw)
        return bool(expected_key and expected_key in actual_key)

    def _canonical_expression_key(self, value: str) -> str:
        try:
            import sqlglot
            from sqlglot import exp

            statement = sqlglot.parse_one(
                f"SELECT {value}",
                read=self.dialect,
            )
            if isinstance(statement, exp.Select) and statement.expressions:
                value = statement.expressions[0].sql(dialect=self.dialect)
        except Exception:
            pass
        return re.sub(r"\s+", "", value.lower())

    def _member_identifiers(self, member: str, alias: str | None = None) -> set[str]:
        candidates = {self._identifier(member)}
        terminal = member.rsplit(".", 1)[-1]
        candidates.add(self._identifier(terminal))
        if alias:
            candidates.add(self._identifier(alias))
        return {candidate for candidate in candidates if candidate}

    def _predicates(
        self,
        group: SemanticFilterGroup | None,
    ) -> list[SemanticPredicate]:
        if group is None:
            return []
        result: list[SemanticPredicate] = []
        for expression in group.expressions:
            if isinstance(expression, SemanticPredicate):
                result.append(expression)
            else:
                result.extend(self._predicates(expression))
        return result

    def _apply_time_window(
        self,
        spec: SemanticQuerySpec,
        operation: QuerySpecPatchOperation,
    ) -> None:
        targets = list(spec.time_filters)
        if operation.member:
            targets = [item for item in targets if item.member == operation.member]
            if not targets and spec.time_filters:
                raise ValueError(
                    f"Time member {operation.member!r} is not present in the current query spec"
                )
        elif len(targets) > 1 and operation.scope not in {"all", "all_periods"}:
            raise ValueError(
                "A time-window patch must name the target member when the query has multiple "
                "time filters"
            )
        if not targets:
            member = operation.member or "__default_time__"
            targets = [
                SemanticTimeFilter(
                    member=member,
                    range=SemanticTimeRange(type="relative", unit=operation.unit),
                )
            ]
            spec.time_filters.extend(targets)
        for time_filter in targets:
            current = time_filter.range.value or time_filter.range.periods
            value = self._apply_numeric(current, operation.operation, operation.value, minimum=1)
            time_filter.range.unit = operation.unit or time_filter.range.unit
            if time_filter.range.type == "closed_calendar_period":
                time_filter.range.periods = value
            else:
                time_filter.range.value = value

    def _apply_filter(
        self,
        spec: SemanticQuerySpec,
        operation: QuerySpecPatchOperation,
    ) -> None:
        group = spec.filters or SemanticFilterGroup(operator=FilterBooleanOperator.AND)
        member = operation.member or operation.to_member
        previous_member = operation.from_member or member
        if operation.operation in {"remove", "replace", "set"} and previous_member:
            group = self._remove_filter_member(group, previous_member)
        if operation.operation in {"add", "replace", "set"} and member:
            values = operation.values or ([] if operation.value is None else [operation.value])
            predicate = SemanticPredicate(
                member=member,
                operator=operation.predicate_operator or "equals",
                values=values,
                source="human_feedback",
            )
            group = group.model_copy(update={"expressions": [*group.expressions, predicate]})
        spec.filters = group if group.expressions else None

    def _remove_filter_member(
        self,
        group: SemanticFilterGroup,
        member: str,
    ) -> SemanticFilterGroup:
        expressions: list[SemanticPredicate | SemanticFilterGroup] = []
        for expression in group.expressions:
            if isinstance(expression, SemanticPredicate):
                if expression.member != member:
                    expressions.append(expression)
                continue
            nested = self._remove_filter_member(expression, member)
            if nested.expressions:
                expressions.append(nested)
        return group.model_copy(update={"expressions": expressions})

    def _apply_measure(
        self,
        spec: SemanticQuerySpec,
        operation: QuerySpecPatchOperation,
    ) -> tuple[set[str], str | None]:
        measures = list(spec.measures)
        removed_aliases: set[str] = set()
        target = operation.to_member or operation.member or (
            str(operation.value) if operation.value is not None else None
        )
        if operation.operation in {"remove", "replace", "set"}:
            from_member = operation.from_member
            if from_member:
                kept: list[SemanticMeasure] = []
                for measure in measures:
                    if measure.member == from_member or measure.alias == from_member:
                        removed_aliases.add(self._identifier(measure.alias or measure.member))
                    else:
                        kept.append(measure)
                measures = kept
            elif len(measures) == 1:
                measure = measures[0]
                removed_aliases.add(self._identifier(measure.alias or measure.member))
                measures = []
            elif len(measures) > 1 and operation.operation == "replace":
                raise ValueError(
                    "Metric replacement must include from_member when the query has multiple "
                    "measures"
                )
            elif operation.operation == "set":
                for measure in measures:
                    removed_aliases.add(self._identifier(measure.alias or measure.member))
                measures = []
        replacement_alias = None
        if operation.operation in {"add", "replace", "set"} and target:
            replacement_alias = self._identifier(target)
            measures.append(SemanticMeasure(member=target, alias=replacement_alias))
        spec.measures = measures
        return removed_aliases, replacement_alias

    def _apply_dimension(self, spec: SemanticQuerySpec, operation: QuerySpecPatchOperation) -> None:
        dimensions = list(spec.dimensions)
        target = operation.to_member or operation.member or (
            str(operation.value) if operation.value is not None else None
        )
        if operation.operation in {"remove", "replace", "set"}:
            from_member = operation.from_member or operation.member
            if from_member:
                dimensions = [item for item in dimensions if item.member != from_member]
            elif operation.operation == "replace" and len(dimensions) > 1:
                raise ValueError(
                    "Dimension replacement must include from_member when the query has multiple "
                    "dimensions"
                )
            elif operation.operation in {"replace", "set"}:
                dimensions = []
        if operation.operation in {"add", "replace", "set"} and target:
            dimensions.append(SemanticDimension(member=target, alias=self._identifier(target)))
        spec.dimensions = dimensions

    def _apply_order(self, spec: SemanticQuerySpec, operation: QuerySpecPatchOperation) -> None:
        orders = list(spec.order_by)
        member = operation.to_member or operation.member or (
            str(operation.value) if operation.value is not None else None
        )
        if operation.operation in {"remove", "replace", "set", "reorder"}:
            target = operation.from_member or operation.member
            if target:
                orders = [item for item in orders if item.member != target]
            elif operation.operation in {"set", "reorder"}:
                orders = []
        if operation.operation in {"add", "replace", "set", "reorder"} and member:
            orders.append(SemanticOrder(member=member, direction=operation.direction or "asc"))
        spec.order_by = orders

    def _apply_source(self, spec: SemanticQuerySpec, operation: QuerySpecPatchOperation) -> None:
        target = operation.to_member or operation.member or (
            str(operation.value) if operation.value is not None else None
        )
        if operation.operation in {"replace", "set"} and target:
            spec.source_objects = [target]
            spec.semantic_model = target
        elif operation.operation == "add" and target and target not in spec.source_objects:
            spec.source_objects.append(target)
        elif operation.operation == "remove" and target:
            spec.source_objects = [item for item in spec.source_objects if item != target]

    @staticmethod
    def _apply_numeric(
        current: int | None,
        operation: str,
        requested: Any,
        *,
        minimum: int,
    ) -> int:
        value = int(requested)
        if operation == "increase":
            value = int(current or 0) + value
        elif operation == "decrease":
            value = int(current or 0) - value
        return max(minimum, value)

    def _filters(
        self,
        filters: Any,
        *,
        excluded_time_members: Iterable[str] | None = None,
    ) -> SemanticFilterGroup | None:
        temporal_members = self._temporal_member_keys(excluded_time_members or [])
        if isinstance(filters, dict) and "operator" in filters and "expressions" in filters:
            group = SemanticFilterGroup.model_validate(filters)
            return self._remove_temporal_predicates(group, temporal_members)
        predicates: list[SemanticPredicate] = []
        for item in filters or []:
            if not isinstance(item, dict):
                continue
            raw_value = item.get("values") or item.get("value")
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            predicate = SemanticPredicate(
                member=str(item.get("member") or item.get("field") or "unknown"),
                operator=str(item.get("operator") or "equals"),
                values=[value for value in values if value is not None],
                source=str(item.get("source") or "user"),
            )
            if self._is_duplicate_temporal_predicate(predicate, temporal_members):
                continue
            predicates.append(predicate)
        return (
            SemanticFilterGroup(operator=FilterBooleanOperator.AND, expressions=predicates)
            if predicates
            else None
        )

    def _normalize_spec(
        self,
        spec: SemanticQuerySpec,
        *,
        sql: str = "",
    ) -> SemanticQuerySpec:
        """Migrate persisted specs to the canonical filter separation.

        Older runs stored the two SQL date-bound predicates both in ``filters`` and in
        ``time_filters``. Those predicates are semantically identical but their rendered SQL can
        differ (for example ``::date`` versus ``CAST(... AS DATE)``), which caused false alignment
        failures. Time constraints now have one canonical owner: ``time_filters``.
        """
        declared_time_members = [item.member for item in spec.time_filters]
        if "__default_time__" in declared_time_members:
            declared_time_members.extend(self._date_filter_members(sql))
        temporal_members = self._temporal_member_keys(declared_time_members)
        normalized_filters = self._remove_temporal_predicates(
            spec.filters,
            temporal_members,
        )
        if normalized_filters == spec.filters and spec.schema_version == "1.1":
            return spec
        return spec.model_copy(
            update={
                "schema_version": "1.1",
                "filters": normalized_filters,
            }
        )

    def _remove_temporal_predicates(
        self,
        group: SemanticFilterGroup | None,
        temporal_members: set[str],
    ) -> SemanticFilterGroup | None:
        if group is None or not temporal_members:
            return group
        expressions: list[SemanticPredicate | SemanticFilterGroup] = []
        for expression in group.expressions:
            if isinstance(expression, SemanticPredicate):
                if self._is_duplicate_temporal_predicate(expression, temporal_members):
                    continue
                expressions.append(expression)
                continue
            nested = self._remove_temporal_predicates(expression, temporal_members)
            if nested is not None:
                expressions.append(nested)
        if not expressions:
            return None
        return group.model_copy(update={"expressions": expressions})

    def _is_duplicate_temporal_predicate(
        self,
        predicate: SemanticPredicate,
        temporal_members: set[str],
    ) -> bool:
        if not temporal_members:
            return False
        member_keys = self._member_identifiers(predicate.member)
        if not member_keys.intersection(temporal_members):
            return False
        operator = predicate.operator.strip().lower().replace("-", "_").replace(" ", "_")
        return operator in {
            "=",
            "==",
            "equals",
            "equal",
            ">",
            ">=",
            "<",
            "<=",
            "gt",
            "gte",
            "lt",
            "lte",
            "greater_than",
            "greater_than_or_equal",
            "less_than",
            "less_than_or_equal",
            "after",
            "before",
            "on_or_after",
            "on_or_before",
            "between",
            "date_range",
            "in_date_range",
        }

    def _temporal_member_keys(self, members: Iterable[str]) -> set[str]:
        keys: set[str] = set()
        for member in members:
            if not member or member == "__default_time__":
                continue
            keys.update(self._member_identifiers(str(member)))
        return keys

    def _time_filters(self, raw_window: Any, sql: str) -> list[SemanticTimeFilter]:
        if isinstance(raw_window, list):
            return [SemanticTimeFilter.model_validate(item) for item in raw_window]
        members = self._date_filter_members(sql) or ["__default_time__"]
        if not raw_window:
            return []
        if isinstance(raw_window, dict) and "member" in raw_window and "range" in raw_window:
            return [SemanticTimeFilter.model_validate(raw_window)]
        raw = dict(raw_window or {})
        label = str(raw.get("label") or "")
        grain = str(raw.get("grain") or "").lower() or None
        count = self._first_int(label) or self._first_int(str(raw.get("start_expression") or ""))
        closed = raw.get("closed_period")
        range_type = "closed_calendar_period" if closed else "relative"
        range_payload = SemanticTimeRange(
            type=range_type,
            unit=grain,
            value=count if range_type != "closed_calendar_period" else None,
            periods=count if range_type == "closed_calendar_period" else None,
            start=raw.get("start_expression"),
            end=raw.get("end_expression"),
            exclude_current_period=bool(closed) if closed is not None else None,
        )
        timezone = self._timezone(sql)
        return [
            SemanticTimeFilter(
                member=member,
                range=range_payload.model_copy(deep=True),
                timezone=timezone,
            )
            for member in members
        ]

    def _orders(self, ordering: Any, sql: str) -> list[SemanticOrder]:
        result: list[SemanticOrder] = []
        if isinstance(ordering, list):
            for item in ordering:
                if isinstance(item, dict):
                    member = item.get("member") or item.get("field")
                    if member:
                        result.append(
                            SemanticOrder(
                                member=str(member),
                                direction=str(item.get("direction") or "asc").lower(),
                            )
                        )
                elif isinstance(item, str):
                    parts = item.rsplit(" ", 1)
                    direction = parts[-1].lower() if parts[-1].lower() in {"asc", "desc"} else "asc"
                    member = parts[0] if direction != "asc" or parts[-1].lower() == "asc" else item
                    result.append(SemanticOrder(member=member, direction=direction))
        if result:
            return result
        return self._orders_from_sql(sql)

    def _orders_from_sql(self, sql: str) -> list[SemanticOrder]:
        if not sql:
            return []
        try:
            import sqlglot
            from sqlglot import exp

            tree = sqlglot.parse_one(sql, read=self.dialect)
            root = tree if isinstance(tree, exp.Select) else next(tree.find_all(exp.Select), None)
            order = root.args.get("order") if root is not None else None
            result: list[SemanticOrder] = []
            for ordered in list(getattr(order, "expressions", []) or []):
                expression = ordered.this if isinstance(ordered, exp.Ordered) else ordered
                member = expression.sql(dialect=self.dialect)
                result.append(
                    SemanticOrder(
                        member=member,
                        direction="desc" if ordered.args.get("desc") else "asc",
                    )
                )
            return result
        except Exception:
            return []

    def _projection_aliases(self, sql: str) -> list[str]:
        if not sql:
            return []
        try:
            import sqlglot
            from sqlglot import exp

            tree = sqlglot.parse_one(sql, read=self.dialect)
            root = tree if isinstance(tree, exp.Select) else next(tree.find_all(exp.Select), None)
            return [projection.alias for projection in root.expressions if projection.alias]
        except Exception:
            return []

    def _date_filter_members(self, sql: str) -> list[str]:
        if not sql:
            return []
        try:
            import sqlglot
            from sqlglot import exp

            tree = sqlglot.parse_one(sql, read=self.dialect)
            members: list[str] = []
            for where in tree.find_all(exp.Where):
                for column in where.find_all(exp.Column):
                    name = column.sql(dialect=self.dialect)
                    lowered = column.name.lower()
                    if any(
                        token in lowered
                        for token in ("date", "time", "timestamp", "month", "day")
                    ):
                        if name not in members:
                            members.append(name)
            return members
        except Exception:
            return []

    def _limit(self, sql: str) -> int | None:
        if not sql:
            return None
        try:
            import sqlglot
            from sqlglot import exp

            tree = sqlglot.parse_one(sql, read=self.dialect)
            limit = tree.args.get("limit")
            expression = limit.args.get("expression") if limit is not None else None
            if isinstance(expression, exp.Literal) and expression.is_int:
                return int(expression.this)
        except Exception:
            return None
        return None

    def _timezone(self, sql: str) -> str | None:
        match = re.search(r"[A-Za-z_]+/[A-Za-z_]+", sql or "")
        return match.group(0) if match else None

    @staticmethod
    def _first_int(value: str) -> int | None:
        match = re.search(r"\b(\d+)\b", value or "")
        return int(match.group(1)) if match else None

    @staticmethod
    def _enum_value(value: Any) -> str | None:
        return str(getattr(value, "value", value)) if value is not None else None

    @staticmethod
    def _identifier(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_]+", "_", str(value).strip()).strip("_").lower()
        return normalized or "member"

    @staticmethod
    def _spec_id(question: str, sources: list[str]) -> str:
        raw = "|".join([question.strip(), *sources]) or "semantic-query"
        return "qs-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
