from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

from axiz.pe.sql_agent.models.contracts import SecurityValidation
from axiz.pe.sql_agent.tools.sql_dialect_normalizer import SqlDialectNormalizer


class SqlSecurityValidator:
    WRITE_EXPRESSIONS = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        exp.Merge,
        exp.Command,
    )

    def __init__(self, dialect: str, max_rows: int) -> None:
        self.dialect = dialect
        self.max_rows = max_rows
        self.normalizer = SqlDialectNormalizer(dialect)

    def validate(
        self,
        sql: str,
        *,
        allowed_sources: list[str],
        policy: dict[str, Any],
        source_contracts: dict[str, dict[str, Any]] | None = None,
    ) -> SecurityValidation:
        violations: list[str] = []
        required_filter_columns = [
            str(value).lower() for value in policy.get("required_filter_columns", [])
        ]
        denied_schemas = [str(value).lower() for value in policy.get("denied_schemas", [])]
        denied_functions = [str(value).lower() for value in policy.get("denied_functions", [])]
        reject_cross_joins = bool(policy.get("reject_cross_joins", True))
        normalized_input = self.normalizer.normalize(sql)
        normalized_sql = (normalized_input.sql or "").strip()
        if not normalized_sql:
            return SecurityValidation(
                approved=False,
                violations=["SQL statement is empty after dialect normalization"],
                max_rows=self.max_rows,
                required_filter_columns=required_filter_columns,
                denied_schemas=denied_schemas,
                denied_functions=denied_functions,
                reject_cross_joins=reject_cross_joins,
            )
        try:
            parsed_statements = sqlglot.parse(normalized_sql, read=self.dialect)
            # sqlglot can represent empty or comment-only fragments as None. Filter them before
            # accessing expression attributes so malformed LLM output fails closed instead of
            # raising an implementation error such as: 'NoneType' object has no attribute 'key'.
            statements = [statement for statement in parsed_statements if statement is not None]
        except sqlglot.errors.ParseError as exc:
            transformations = (
                ", ".join(normalized_input.transformations)
                if normalized_input.transformations
                else "none"
            )
            return SecurityValidation(
                approved=False,
                violations=[
                    "SQL parse error after dialect normalization "
                    f"({transformations}): {exc}"
                ],
                max_rows=self.max_rows,
                required_filter_columns=required_filter_columns,
                denied_schemas=denied_schemas,
                denied_functions=denied_functions,
                reject_cross_joins=reject_cross_joins,
            )

        if len(statements) != 1:
            violations.append(
                "Exactly one non-empty SQL statement is allowed"
                if statements
                else "SQL statement is empty or contains only comments"
            )
            return SecurityValidation(
                approved=False,
                violations=violations,
                max_rows=self.max_rows,
                required_filter_columns=required_filter_columns,
                denied_schemas=denied_schemas,
                denied_functions=denied_functions,
                reject_cross_joins=reject_cross_joins,
            )

        tree = statements[0]
        statement_type = str(tree.key or tree.__class__.__name__).upper()
        if isinstance(tree, self.WRITE_EXPRESSIONS) or any(
            tree.find(expression_type) is not None for expression_type in self.WRITE_EXPRESSIONS
        ):
            violations.append("Only read-only SELECT queries are allowed")

        if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)) and tree.find(exp.Select) is None:
            violations.append("The statement must contain a SELECT query")

        cte_aliases = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        physical_tables = [
            table
            for table in tree.find_all(exp.Table)
            if not (
                not table.catalog
                and not table.db
                and table.name.lower() in cte_aliases
            )
        ]
        tables = sorted({self._table_name(table) for table in physical_tables})
        allowed = {source.lower() for source in allowed_sources}
        unauthorized = [table for table in tables if table.lower() not in allowed]
        if unauthorized:
            violations.append(f"Unauthorized sources: {', '.join(unauthorized)}")

        contract_violations = self._validate_source_contracts(
            tree,
            tables=tables,
            source_contracts=source_contracts or {},
        )
        violations.extend(contract_violations)

        denied_schema_set = set(denied_schemas)
        for table in physical_tables:
            schema = str(table.db or "").lower()
            if schema in denied_schema_set:
                violations.append(f"Schema is denied: {schema}")

        if reject_cross_joins:
            for join in tree.find_all(exp.Join):
                has_condition = join.args.get("on") is not None or join.args.get("using") is not None
                if str(join.args.get("kind") or "").upper() == "CROSS" or not has_condition:
                    violations.append("CROSS or conditionless joins are not allowed")

        denied_function_set = set(denied_functions)
        for function in tree.find_all(exp.Func):
            function_name = function.sql_name().lower()
            if function_name in denied_function_set:
                violations.append(f"Function is denied: {function_name}")

        where_sql = " ".join(
            where.sql(dialect=self.dialect).lower() for where in tree.find_all(exp.Where)
        )
        enforce_temporal_filter = bool(policy.get("enforce_temporal_filter", False))
        if enforce_temporal_filter and required_filter_columns and not any(
            column in where_sql for column in required_filter_columns
        ):
            violations.append(
                "An explicitly enforced temporal filter is required using one of: "
                + ", ".join(required_filter_columns)
            )

        normalized = tree.sql(dialect=self.dialect, pretty=True)
        normalized = self._enforce_limit(normalized)
        enforced_limit = self._read_limit(normalized)
        columns = sorted({column.sql(dialect=self.dialect) for column in tree.find_all(exp.Column)})
        return SecurityValidation(
            approved=not violations,
            normalized_sql=normalized if not violations else None,
            violations=violations,
            tables=tables,
            columns=columns,
            statement_type=statement_type,
            max_rows=self.max_rows,
            enforced_limit=enforced_limit,
            required_filter_columns=required_filter_columns,
            denied_schemas=denied_schemas,
            denied_functions=denied_functions,
            reject_cross_joins=reject_cross_joins,
        )

    def _validate_source_contracts(
        self,
        tree: exp.Expression,
        *,
        tables: list[str],
        source_contracts: dict[str, dict[str, Any]],
    ) -> list[str]:
        """Validate columns and categorical literals against the selected semantic source.

        The deterministic check is intentionally strict only for a single physical source without
        CTEs. Multi-source and CTE queries remain covered by PostgreSQL EXPLAIN and the allowlist,
        avoiding false positives for derived columns while closing the common cross-view column
        hallucination failure mode.
        """
        if len(tables) != 1 or any(tree.find_all(exp.CTE)):
            return []
        source = tables[0]
        contract = source_contracts.get(source) or source_contracts.get(source.lower())
        if not contract:
            return []
        allowed_columns = {
            str(column).lower() for column in contract.get("columns", []) if column
        }
        if not allowed_columns:
            return []
        select_aliases = {
            str(projection.alias).lower()
            for select in tree.find_all(exp.Select)
            for projection in select.expressions
            if projection.alias
        }
        unknown = sorted(
            {
                column.name
                for column in tree.find_all(exp.Column)
                if column.name
                and column.name.lower() not in allowed_columns
                and column.name.lower() not in select_aliases
            }
        )
        violations: list[str] = []
        if unknown:
            preview = ", ".join(sorted(allowed_columns)[:40])
            violations.append(
                f"Unknown columns for {source}: {', '.join(unknown)}. "
                f"Published columns: {preview}"
            )

        allowed_values = {
            str(column).lower(): {str(value) for value in values}
            for column, values in (contract.get("allowed_values") or {}).items()
            if isinstance(values, list)
        }
        invalid_values: list[str] = []
        for equality in tree.find_all(exp.EQ):
            left, right = equality.this, equality.expression
            if not isinstance(left, exp.Column) or not isinstance(right, exp.Literal):
                continue
            permitted = allowed_values.get(left.name.lower())
            if permitted is not None and str(right.this) not in permitted:
                invalid_values.append(
                    f"{left.name}={right.this!r} (allowed: {', '.join(sorted(permitted))})"
                )
        for predicate in tree.find_all(exp.In):
            column = predicate.this
            if not isinstance(column, exp.Column):
                continue
            permitted = allowed_values.get(column.name.lower())
            if permitted is None:
                continue
            for value in predicate.expressions:
                if isinstance(value, exp.Literal) and str(value.this) not in permitted:
                    invalid_values.append(
                        f"{column.name}={value.this!r} (allowed: {', '.join(sorted(permitted))})"
                    )
        if invalid_values:
            violations.append("Invalid categorical values: " + "; ".join(invalid_values))
        return violations

    def _enforce_limit(self, sql: str) -> str:
        canonical = self.normalizer.normalize(sql).sql
        tree = sqlglot.parse_one(canonical, read=self.dialect)
        current_limit = tree.args.get("limit")
        should_replace = current_limit is None
        if current_limit is not None:
            expression = current_limit.args.get("expression")
            if not isinstance(expression, exp.Literal) or not expression.is_int:
                should_replace = True
            else:
                should_replace = int(expression.this) > self.max_rows
        if should_replace:
            tree.set("limit", exp.Limit(expression=exp.Literal.number(self.max_rows)))
        return tree.sql(dialect=self.dialect, pretty=True)

    def _read_limit(self, sql: str) -> int | None:
        canonical = self.normalizer.normalize(sql).sql
        tree = sqlglot.parse_one(canonical, read=self.dialect)
        limit = tree.args.get("limit")
        expression = limit.args.get("expression") if limit is not None else None
        if isinstance(expression, exp.Literal) and expression.is_int:
            return int(expression.this)
        return None

    @staticmethod
    def _table_name(table: exp.Table) -> str:
        parts = [str(value) for value in (table.catalog, table.db, table.name) if value]
        return ".".join(parts)
