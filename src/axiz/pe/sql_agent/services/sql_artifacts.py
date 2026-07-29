from __future__ import annotations

import hashlib
from typing import Any

from axiz.pe.sql_agent.models.sql_artifacts import (
    CompiledSqlArtifact,
    CompiledSqlValidation,
    SqlSnapshot,
)
from axiz.pe.sql_agent.tools.sql_ast_analyzer import SqlAstAnalyzer


class SqlArtifactService:
    """Build generic SQL snapshots without encoding business-specific feedback rules."""

    def __init__(self, dialect: str = "postgres") -> None:
        self.dialect = dialect
        self.analyzer = SqlAstAnalyzer(dialect=dialect)

    @staticmethod
    def _normalized_identifier(value: str) -> str:
        return value.strip().strip('"').lower()

    def snapshot(self, sql: str) -> SqlSnapshot:
        from sqlglot import exp

        tree = self.analyzer.parse(sql)
        root = self.analyzer.root_select(tree)
        if root is None:
            raise ValueError("The SQL does not contain a SELECT statement")

        projections = [item.sql(dialect=self.dialect) for item in root.expressions]
        where = root.args.get("where")
        predicates: list[str] = []
        if where is not None:
            condition = where.this if isinstance(where, exp.Where) else where
            predicates = [item.sql(dialect=self.dialect) for item in self._flatten_and(condition)]

        group = root.args.get("group")
        group_by = [
            item.sql(dialect=self.dialect)
            for item in list(getattr(group, "expressions", []) or [])
        ]
        having_node = root.args.get("having")
        having = None
        if having_node is not None:
            having_expression = (
                having_node.this if isinstance(having_node, exp.Having) else having_node
            )
            having = having_expression.sql(dialect=self.dialect)

        order = root.args.get("order")
        order_by = [
            item.sql(dialect=self.dialect)
            for item in list(getattr(order, "expressions", []) or [])
        ]
        limit = self._limit_value(root)
        ctes = [cte.alias_or_name for cte in tree.find_all(exp.CTE) if cte.alias_or_name]
        return SqlSnapshot(
            dialect=self.dialect,
            statement_type=tree.key.upper(),
            sources=self.analyzer.sources(tree),
            projections=projections,
            predicates=predicates,
            group_by=group_by,
            having=having,
            order_by=order_by,
            limit=limit,
            distinct=bool(root.args.get("distinct")),
            ctes=ctes,
        )

    @staticmethod
    def _flatten_and(expression: Any) -> list[Any]:
        from sqlglot import exp

        if isinstance(expression, exp.And):
            return [
                *SqlArtifactService._flatten_and(expression.this),
                *SqlArtifactService._flatten_and(expression.expression),
            ]
        return [expression]

    @staticmethod
    def _limit_value(select: Any) -> int | None:
        from sqlglot import exp

        limit = select.args.get("limit")
        if limit is None:
            return None
        expression = limit.expression if isinstance(limit, exp.Limit) else limit
        if isinstance(expression, exp.Literal) and expression.is_int:
            return int(expression.this)
        return None

    def validate_structure(self, sql: str) -> CompiledSqlValidation:
        from sqlglot import exp

        violations: list[str] = []
        aliases: list[str] = []
        invalid_order: list[str] = []
        try:
            tree = self.analyzer.parse(sql)
            root = self.analyzer.root_select(tree)
            if root is None:
                raise ValueError("The SQL does not contain a SELECT statement")

            aliases = [
                self._normalized_identifier(item.alias_or_name)
                for item in root.expressions
                if item.alias_or_name
            ]
            source_columns = {
                self._normalized_identifier(column.name)
                for column in tree.find_all(exp.Column)
            }
            order = root.args.get("order")
            for ordered in list(getattr(order, "expressions", []) or []):
                expression = ordered.this if isinstance(ordered, exp.Ordered) else ordered
                if isinstance(expression, exp.Column):
                    name = self._normalized_identifier(expression.name)
                    if name not in aliases and name not in source_columns:
                        invalid_order.append(expression.sql(dialect=self.dialect))
            if invalid_order:
                violations.append(
                    "ORDER BY references unresolved identifiers: " + ", ".join(invalid_order)
                )
            return CompiledSqlValidation(
                parse_valid=True,
                references_valid=not invalid_order,
                projection_aliases=aliases,
                invalid_order_references=invalid_order,
                violations=violations,
            )
        except Exception as exc:
            return CompiledSqlValidation(
                parse_valid=False,
                references_valid=False,
                projection_aliases=aliases,
                invalid_order_references=invalid_order,
                violations=[f"SQL parsing failed: {exc}"],
            )

    def compile(self, sql: str) -> CompiledSqlArtifact:
        snapshot = self.snapshot(sql)
        validation = self.validate_structure(sql)
        return CompiledSqlArtifact(
            dialect=self.dialect,
            sql=sql,
            sql_hash=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            snapshot=snapshot,
            validation=validation,
            execution_state="candidate",
        )
