from axiz.pe.sql_agent.models.contracts import QueryResult
from axiz.pe.sql_agent.tools.chart_builder import ChartBuilderTool


def test_chart_builder_uses_line_for_date_series() -> None:
    result = QueryResult(
        columns=["metric_date", "processed_amount_pen"],
        rows=[{"metric_date": "2026-07-01", "processed_amount_pen": 100.0}],
        row_count=1,
        elapsed_ms=1.0,
    )
    spec = ChartBuilderTool().build(result, "Evolution")
    assert spec.type == "line"
    assert spec.x == "metric_date"
    assert spec.y == ["processed_amount_pen"]
