from __future__ import annotations

from typing import Any

from axiz.pe.sql_agent.models.contracts import QueryFilter, TimeWindowContext


class SqlMemoryExtractor:
    """Extracts bounded filter and time-window facts from validated SQL."""

    def __init__(self, dialect: str) -> None:
        self.dialect = dialect

    def extract(
        self,
        sql: str | None,
    ) -> tuple[list[QueryFilter], TimeWindowContext | None]:
        if not sql:
            return [], None
        try:
            from sqlglot import exp, parse_one

            statement = parse_one(sql, read=self.dialect)
        except Exception:
            return [], None

        where = statement.find(exp.Where)
        if where is None:
            return [], None

        filters: list[QueryFilter] = []
        start_expression: str | None = None
        end_expression: str | None = None
        time_field: str | None = None

        for predicate in self._predicates(where.this, exp):
            extracted = self._filter(predicate, exp)
            if extracted is None:
                continue
            filters.append(extracted)
            field_lower = extracted.field.lower()
            if any(token in field_lower for token in ("date", "month", "day", "time")):
                time_field = extracted.field
                if extracted.operator in {">", ">="}:
                    start_expression = extracted.value
                elif extracted.operator in {"<", "<="}:
                    end_expression = extracted.value

        window = None
        if time_field and (start_expression or end_expression):
            from axiz.pe.sql_agent.tools.sql_feedback import SqlFeedbackApplier

            day_count = SqlFeedbackApplier.rolling_day_window_days(
                sql,
                dialect=self.dialect,
            )
            month_count = SqlFeedbackApplier.closed_month_window_months(
                sql,
                dialect=self.dialect,
            )
            if day_count is not None:
                label = (
                    "Último día calendario completo"
                    if day_count == 1
                    else f"Últimos {day_count} días calendario completos"
                )
                grain = "day"
                closed_period = True
            elif month_count is not None:
                label = (
                    "Último mes calendario completamente cerrado"
                    if month_count == 1
                    else f"Últimos {month_count} meses calendario completamente cerrados"
                )
                grain = "month"
                closed_period = True
            else:
                label = f"Periodo derivado del SQL sobre {time_field}"
                grain = None
                closed_period = None
            window = TimeWindowContext(
                label=label,
                start_expression=start_expression,
                end_expression=end_expression,
                grain=grain,
                closed_period=closed_period,
            )
        return filters, window

    def extract_query_contract(
        self,
        sql: str | None,
    ) -> tuple[list[str], int | None, list[str]]:
        """Extract ordering, limit and sources needed to preserve follow-up invariants."""
        if not sql:
            return [], None, []
        try:
            from sqlglot import exp, parse_one

            statement = parse_one(sql, read=self.dialect)
        except Exception:
            return self._extract_query_contract_text(sql)

        select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
        ordering: list[str] = []
        if select is not None and select.args.get("order") is not None:
            ordering = [
                item.sql(dialect=self.dialect)
                for item in select.args["order"].expressions
            ]

        limit_value: int | None = None
        limit = statement.args.get("limit")
        expression = limit.args.get("expression") if limit is not None else None
        if isinstance(expression, exp.Literal) and expression.is_int:
            limit_value = int(expression.this)

        sources: list[str] = []
        for table in statement.find_all(exp.Table):
            name = table.sql(dialect=self.dialect)
            if name not in sources:
                sources.append(name)
        return ordering, limit_value, sources

    @staticmethod
    def _extract_query_contract_text(sql: str) -> tuple[list[str], int | None, list[str]]:
        """Strict metadata fallback used only when SQLGlot is unavailable."""
        import re

        limit_match = re.search(r"(?is)\bLIMIT\s+(\d+)\b", sql)
        limit_value = int(limit_match.group(1)) if limit_match else None

        ordering: list[str] = []
        order_match = re.search(
            r"(?is)\bORDER\s+BY\s+(.*?)(?:\bLIMIT\b|$)",
            sql,
        )
        if order_match:
            ordering = [
                item.strip()
                for item in order_match.group(1).split(",")
                if item.strip()
            ]

        sources: list[str] = []
        for match in re.finditer(
            r"(?ix)\b(?:FROM|JOIN)\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)",
            sql,
        ):
            source = match.group(1)
            if source not in sources:
                sources.append(source)
        return ordering, limit_value, sources

    @staticmethod
    def _predicates(expression: Any, exp: Any) -> list[Any]:
        if isinstance(expression, exp.And):
            return [
                *SqlMemoryExtractor._predicates(expression.left, exp),
                *SqlMemoryExtractor._predicates(expression.right, exp),
            ]
        return [expression]

    def _filter(self, predicate: Any, exp: Any) -> QueryFilter | None:
        operators = (
            (exp.EQ, "="),
            (exp.NEQ, "!="),
            (exp.GT, ">"),
            (exp.GTE, ">="),
            (exp.LT, "<"),
            (exp.LTE, "<="),
            (exp.Like, "LIKE"),
            (exp.ILike, "ILIKE"),
        )
        for expression_type, operator in operators:
            if isinstance(predicate, expression_type):
                return QueryFilter(
                    field=predicate.left.sql(dialect=self.dialect),
                    operator=operator,
                    value=predicate.right.sql(dialect=self.dialect),
                    source="sql",
                )
        if isinstance(predicate, exp.In):
            values = ", ".join(
                item.sql(dialect=self.dialect) for item in predicate.expressions
            )
            return QueryFilter(
                field=predicate.this.sql(dialect=self.dialect),
                operator="IN",
                value=f"({values})",
                source="sql",
            )
        if isinstance(predicate, exp.Between):
            low = predicate.args["low"].sql(dialect=self.dialect)
            high = predicate.args["high"].sql(dialect=self.dialect)
            return QueryFilter(
                field=predicate.this.sql(dialect=self.dialect),
                operator="BETWEEN",
                value=f"{low} AND {high}",
                source="sql",
            )
        return None
