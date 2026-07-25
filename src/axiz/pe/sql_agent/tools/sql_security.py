from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

from axiz.pe.sql_agent.models.contracts import SecurityValidation


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

    def validate(
        self,
        sql: str,
        *,
        allowed_sources: list[str],
        policy: dict[str, Any],
    ) -> SecurityValidation:
        violations: list[str] = []
        try:
            statements = sqlglot.parse(sql, read=self.dialect)
        except sqlglot.errors.ParseError as exc:
            return SecurityValidation(approved=False, violations=[f"SQL parse error: {exc}"])

        if len(statements) != 1:
            violations.append("Exactly one SQL statement is allowed")
            return SecurityValidation(approved=False, violations=violations)

        tree = statements[0]
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

        denied_schemas = {str(value).lower() for value in policy.get("denied_schemas", [])}
        for table in physical_tables:
            schema = str(table.db or "").lower()
            if schema in denied_schemas:
                violations.append(f"Schema is denied: {schema}")

        if policy.get("reject_cross_joins", True):
            for join in tree.find_all(exp.Join):
                has_condition = join.args.get("on") is not None or join.args.get("using") is not None
                if str(join.args.get("kind") or "").upper() == "CROSS" or not has_condition:
                    violations.append("CROSS or conditionless joins are not allowed")

        denied_functions = {str(value).lower() for value in policy.get("denied_functions", [])}
        for function in tree.find_all(exp.Func):
            function_name = function.sql_name().lower()
            if function_name in denied_functions:
                violations.append(f"Function is denied: {function_name}")

        required_filter_columns = [
            str(value).lower() for value in policy.get("required_filter_columns", [])
        ]
        where_sql = " ".join(
            where.sql(dialect=self.dialect).lower() for where in tree.find_all(exp.Where)
        )
        if required_filter_columns and not any(
            column in where_sql for column in required_filter_columns
        ):
            violations.append(
                "A bounded time filter is required using one of: "
                + ", ".join(required_filter_columns)
            )

        normalized = tree.sql(dialect=self.dialect, pretty=True)
        normalized = self._enforce_limit(normalized)
        columns = sorted({column.sql(dialect=self.dialect) for column in tree.find_all(exp.Column)})
        return SecurityValidation(
            approved=not violations,
            normalized_sql=normalized if not violations else None,
            violations=violations,
            tables=tables,
            columns=columns,
        )

    def _enforce_limit(self, sql: str) -> str:
        tree = sqlglot.parse_one(sql, read=self.dialect)
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

    @staticmethod
    def _table_name(table: exp.Table) -> str:
        parts = [str(value) for value in (table.catalog, table.db, table.name) if value]
        return ".".join(parts)
