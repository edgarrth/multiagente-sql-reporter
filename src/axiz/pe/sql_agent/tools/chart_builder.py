from __future__ import annotations

from axiz.pe.sql_agent.models.contracts import QueryResult, VisualizationSpec


class ChartBuilderTool:
    def build(self, result: QueryResult, title: str) -> VisualizationSpec:
        if not result.rows or len(result.columns) < 2:
            return VisualizationSpec(type="table", title=title)

        first = result.rows[0]
        numeric = [
            column
            for column in result.columns
            if isinstance(first.get(column), (int, float)) and not isinstance(first.get(column), bool)
        ]
        categorical = [column for column in result.columns if column not in numeric]
        if numeric and categorical:
            chart_type = "line" if "date" in categorical[0].lower() else "bar"
            return VisualizationSpec(
                type=chart_type,
                title=title,
                x=categorical[0],
                y=numeric[:3],
            )
        return VisualizationSpec(type="table", title=title)
