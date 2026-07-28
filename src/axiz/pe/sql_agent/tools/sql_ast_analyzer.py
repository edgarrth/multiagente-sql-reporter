from __future__ import annotations

"""Generic SQL structure analysis backed by SQLGlot.

The module understands SQL syntax, not business vocabulary. It is intentionally deterministic and
contains no domain-specific rules. Text fallback belongs to callers and is never used in the
production image, where SQLGlot is a required dependency.
"""

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class AstInterval:
    value: int
    unit: str
    node: Any


class SqlAstAnalyzer:
    def __init__(self, *, dialect: str = "postgres") -> None:
        self.dialect = dialect

    def parse(self, sql: str) -> Any:
        import sqlglot

        return sqlglot.parse_one(sql, read=self.dialect)

    @staticmethod
    def _node_name(node: Any) -> str:
        name = ""
        sql_name = getattr(node, "sql_name", None)
        if callable(sql_name):
            try:
                name = str(sql_name())
            except Exception:
                name = ""
        # Generic functions such as TIMEZONE(...) may be represented as Anonymous by SQLGlot.
        # In that case use the parsed function identifier rather than the generic node type.
        if name.upper() in {"", "ANONYMOUS", "FUNC"}:
            explicit = getattr(node, "name", "") or getattr(node, "this", "") or ""
            if isinstance(explicit, str):
                name = explicit
        return str(name).upper()

    @staticmethod
    def _literal_text(node: Any) -> str:
        value = getattr(node, "this", node)
        nested = getattr(value, "this", value)
        return str(nested or "").strip()

    @classmethod
    def _interval_value(cls, interval: Any) -> tuple[int, str] | None:
        unit_expression = interval.args.get("unit")
        unit = str(
            getattr(unit_expression, "name", "")
            or getattr(unit_expression, "this", "")
            or ""
        ).upper()
        raw = cls._literal_text(interval.this)
        if unit:
            try:
                return int(raw), unit.rstrip("S")
            except ValueError:
                return None

        # PostgreSQL may parse INTERVAL '2 MONTHS' with the unit embedded in the literal.
        parts = raw.upper().split()
        if len(parts) != 2:
            return None
        try:
            return int(parts[0]), parts[1].rstrip("S")
        except ValueError:
            return None

    def intervals(self, expression: Any, *, units: set[str] | None = None) -> list[AstInterval]:
        from sqlglot import exp

        normalized_units = {item.upper().rstrip("S") for item in units or set()}
        result: list[AstInterval] = []
        for interval in expression.find_all(exp.Interval):
            parsed = self._interval_value(interval)
            if parsed is None:
                continue
            value, unit = parsed
            if normalized_units and unit not in normalized_units:
                continue
            result.append(AstInterval(value=value, unit=unit, node=interval))
        return result


    def numeric_date_deltas(self, expression: Any) -> list[int]:
        from sqlglot import exp

        values: list[int] = []
        for subtraction in expression.find_all(exp.Sub):
            delta = subtraction.expression
            if not isinstance(delta, exp.Literal) or not delta.is_int:
                continue
            base = subtraction.this
            node_names = {self._node_name(node) for node in base.walk()}
            columns = {column.name.lower() for column in base.find_all(exp.Column)}
            looks_temporal = bool(
                node_names
                & {
                    "CURRENT_DATE",
                    "CURRENT_TIMESTAMP",
                    "TIMEZONE",
                    "DATE_TRUNC",
                    "CAST",
                }
            ) or any(
                token in name
                for name in columns
                for token in ("date", "time", "timestamp", "month", "day")
            )
            if not looks_temporal:
                continue
            value = abs(int(delta.this))
            if value:
                values.append(value)
        return values

    def date_trunc_grains(self, expression: Any) -> set[str]:
        grains: set[str] = set()
        for node in expression.walk():
            if self._node_name(node) != "DATE_TRUNC":
                continue
            # SQLGlot represents DATE_TRUNC with an explicit ``unit`` argument in current
            # versions. Anonymous/provider-specific forms may instead expose positional values.
            candidates: list[Any] = [node.args.get("unit")]
            if getattr(node, "this", None) is not None:
                candidates.append(node.this)
            candidates.extend(list(getattr(node, "expressions", []) or []))
            for candidate in (item for item in candidates if item is not None):
                value = self._literal_text(candidate).strip("'\"").lower()
                if value in {"month", "day", "week", "year"}:
                    grains.add(value)
                    break
        return grains

    def timezone_names(self, expression: Any) -> list[str]:
        names: list[str] = []
        for node in expression.walk():
            if self._node_name(node) not in {"TIMEZONE", "AT TIME ZONE", "ATTIMEZONE"}:
                continue
            values: list[Any] = [node.args.get("zone")]
            if getattr(node, "this", None) is not None:
                values.append(node.this)
            values.extend(list(getattr(node, "expressions", []) or []))
            for value in (item for item in values if item is not None):
                text = self._literal_text(value).strip("'\"")
                if "/" in text and text not in names:
                    names.append(text)
                    break
        return names


    def sources(self, tree: Any) -> list[str]:
        from sqlglot import exp

        result: list[str] = []
        for table in tree.find_all(exp.Table):
            catalog = str(getattr(table, "catalog", "") or "")
            db = str(getattr(table, "db", "") or "")
            name = str(getattr(table, "name", "") or "")
            parts = [item for item in (catalog, db, name) if item]
            source = ".".join(parts)
            if source and source not in result:
                result.append(source)
        return result

    @staticmethod
    def root_select(tree: Any) -> Any | None:
        from sqlglot import exp

        if isinstance(tree, exp.Select):
            return tree
        if isinstance(tree, exp.With):
            return tree.this if isinstance(tree.this, exp.Select) else next(
                tree.find_all(exp.Select), None
            )
        return next(tree.find_all(exp.Select), None)

    def grouped_by_temporal_expression(self, tree: Any, *, grain: str) -> bool:
        from sqlglot import exp

        # Inspect every SELECT, including CTEs and nested queries. Looking only at the outer SELECT
        # misses common shapes such as WITH monthly AS (... GROUP BY metric_month) SELECT ... .
        selects = [tree] if isinstance(tree, exp.Select) else list(tree.find_all(exp.Select))
        for select in selects:
            group = select.args.get("group")
            if group is None:
                continue
            for expression in list(getattr(group, "expressions", []) or []):
                names = {column.name.lower() for column in expression.find_all(exp.Column)}
                if any(grain in name for name in names):
                    return True
                if grain in self.date_trunc_grains(expression):
                    return True
        return False

    def bucket_offsets(self, tree: Any, *, unit: str) -> list[int]:
        from sqlglot import exp

        offsets: list[int] = []
        for node_type in (exp.Filter, exp.Case):
            for node in tree.find_all(node_type):
                for interval in self.intervals(node, units={unit}):
                    if interval.value:
                        offsets.append(abs(interval.value))
        return offsets

    def overall_window_periods(self, tree: Any, *, unit: str) -> int | None:
        from sqlglot import exp

        # The governed range is frequently defined inside a CTE. Restrict extraction to WHERE
        # clauses so FILTER/CASE bucket offsets are not mistaken for the global window.
        selects = [tree] if isinstance(tree, exp.Select) else list(tree.find_all(exp.Select))
        values: list[int] = []
        for select in selects:
            where = select.args.get("where")
            if where is None:
                continue
            values.extend(
                abs(item.value)
                for item in self.intervals(where, units={unit})
                if item.value != 0
            )
            if unit.upper().rstrip("S") == "DAY":
                values.extend(self.numeric_date_deltas(where))
        return max(values) if values else None
