from __future__ import annotations

from typing import Any


class SqlRevisionDiffAnalyzer:
    """Produce a generic AST-level before/after summary for arbitrary SELECT revisions."""

    def __init__(self, dialect: str = "postgres") -> None:
        self.dialect = dialect

    def compare(self, previous_sql: str, revised_sql: str) -> dict[str, Any]:
        try:
            import sqlglot
            from sqlglot import exp

            previous = sqlglot.parse_one(previous_sql, read=self.dialect)
            revised = sqlglot.parse_one(revised_sql, read=self.dialect)
        except Exception as exc:
            return {
                "parse_valid": False,
                "error": str(exc),
                "changed": previous_sql.strip() != revised_sql.strip(),
            }

        before = self._snapshot(previous, exp)
        after = self._snapshot(revised, exp)
        return {
            "parse_valid": True,
            "changed": before != after,
            "before": before,
            "after": after,
            "projection": self._sequence_diff(before["projection"], after["projection"]),
            "sources": self._set_diff(before["sources"], after["sources"]),
            "where_changed": before["where"] != after["where"],
            "group_by_changed": before["group_by"] != after["group_by"],
            "having_changed": before["having"] != after["having"],
            "order_by_changed": before["order_by"] != after["order_by"],
            "limit_changed": before["limit"] != after["limit"],
            "distinct_changed": before["distinct"] != after["distinct"],
        }

    def _snapshot(self, statement: Any, exp: Any) -> dict[str, Any]:
        select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
        if select is None:
            return {
                "projection": [], "sources": [], "where": "", "group_by": [],
                "having": "", "order_by": [], "limit": None, "distinct": False,
            }
        projection = [item.sql(dialect=self.dialect) for item in select.expressions]
        cte_names = {
            str(cte.alias_or_name).strip().lower()
            for cte in statement.find_all(exp.CTE)
            if cte.alias_or_name
        }
        sources = sorted(
            {
                ".".join(str(value) for value in (table.catalog, table.db, table.name) if value)
                for table in statement.find_all(exp.Table)
                if str(table.name).strip().lower() not in cte_names
            }
        )
        where = select.args.get("where")
        group = select.args.get("group")
        having = select.args.get("having")
        order = select.args.get("order")
        limit = statement.args.get("limit") or select.args.get("limit")
        limit_expression = limit.args.get("expression") if limit is not None else None
        limit_value: int | str | None = None
        if isinstance(limit_expression, exp.Literal) and limit_expression.is_int:
            limit_value = int(limit_expression.this)
        elif limit_expression is not None:
            limit_value = limit_expression.sql(dialect=self.dialect)
        return {
            "projection": projection,
            "sources": sources,
            "where": where.sql(dialect=self.dialect) if where is not None else "",
            "group_by": [item.sql(dialect=self.dialect) for item in (group.expressions if group else [])],
            "having": having.sql(dialect=self.dialect) if having is not None else "",
            "order_by": [item.sql(dialect=self.dialect) for item in (order.expressions if order else [])],
            "limit": limit_value,
            "distinct": bool(select.args.get("distinct")),
        }

    @staticmethod
    def _sequence_diff(before: list[str], after: list[str]) -> dict[str, Any]:
        return {
            "before": before,
            "after": after,
            "removed": [item for item in before if item not in after],
            "added": [item for item in after if item not in before],
            "order_changed": before != after and set(before) == set(after),
        }

    @staticmethod
    def _set_diff(before: list[str], after: list[str]) -> dict[str, Any]:
        return {
            "before": before,
            "after": after,
            "removed": sorted(set(before) - set(after)),
            "added": sorted(set(after) - set(before)),
        }
