from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from axiz.pe.sql_agent.tools.sql_ast_analyzer import SqlAstAnalyzer


class TemporalQueryTopology(StrEnum):
    """Structural temporal shape detected from a previously approved SELECT."""

    NONE = "none"
    SINGLE_WINDOW = "single_window"
    COMPARATIVE_BUCKETS = "comparative_buckets"
    PERIOD_SERIES = "period_series"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TemporalQueryShape:
    topology: TemporalQueryTopology = TemporalQueryTopology.NONE
    grain: str | None = None
    overall_periods: int | None = None
    bucket_offsets: tuple[int, ...] = ()
    timezone: str | None = None

    @property
    def is_comparative(self) -> bool:
        return self.topology == TemporalQueryTopology.COMPARATIVE_BUCKETS

    @property
    def comparison_periods(self) -> int | None:
        if not self.is_comparative or len(self.bucket_offsets) < 2:
            return None
        return max(1, len(self.bucket_offsets) - 1)


class TemporalQueryShapeAnalyzer:
    """Classify temporal SQL using the SQLGlot AST.

    The production path does not parse SQL with regular expressions. SQLGlot is authoritative; if
    the parser is unavailable or rejects the statement, the shape is UNKNOWN and no deterministic
    rewrite is attempted.
    """

    @classmethod
    def analyze(cls, sql: str | None, *, dialect: str = "postgres") -> TemporalQueryShape:
        if not sql or not sql.strip():
            return TemporalQueryShape()
        try:
            analyzer = SqlAstAnalyzer(dialect=dialect)
            tree = analyzer.parse(sql)
            return cls._from_ast(analyzer, tree)
        except ImportError:
            return TemporalQueryShape(topology=TemporalQueryTopology.UNKNOWN)
        except Exception:
            return TemporalQueryShape(topology=TemporalQueryTopology.UNKNOWN)

    @classmethod
    def _from_ast(cls, analyzer: SqlAstAnalyzer, tree) -> TemporalQueryShape:
        interval_units = {item.unit for item in analyzer.intervals(tree)}
        trunc_grains = analyzer.date_trunc_grains(tree)
        grain: str | None = None
        if "MONTH" in interval_units or "month" in trunc_grains:
            grain = "month"
        elif (
            "DAY" in interval_units
            or "day" in trunc_grains
            or analyzer.numeric_date_deltas(tree)
        ):
            grain = "day"

        timezone_names = analyzer.timezone_names(tree)
        timezone = timezone_names[0] if timezone_names else None
        if grain is None:
            return TemporalQueryShape(timezone=timezone)

        unit = grain.upper()
        bucket_offsets = tuple(sorted(set(analyzer.bucket_offsets(tree, unit=unit))))
        overall = analyzer.overall_window_periods(tree, unit=unit)

        if analyzer.grouped_by_temporal_expression(tree, grain=grain):
            topology = TemporalQueryTopology.PERIOD_SERIES
        elif len(bucket_offsets) >= 2:
            topology = TemporalQueryTopology.COMPARATIVE_BUCKETS
        elif overall is not None:
            topology = TemporalQueryTopology.SINGLE_WINDOW
        else:
            topology = TemporalQueryTopology.UNKNOWN

        return TemporalQueryShape(
            topology=topology,
            grain=grain,
            overall_periods=overall,
            bucket_offsets=bucket_offsets,
            timezone=timezone,
        )
