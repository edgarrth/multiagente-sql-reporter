from __future__ import annotations

import json

from axiz.pe.sql_agent.models.contracts import (
    CatalogAnswerOutput,
    ExplanationOutput,
    QueryResult,
    VerificationOutput,
    VisualizationSpec,
)
from axiz.pe.sql_agent.services.llm import StructuredLLM
from axiz.pe.sql_agent.tools.chart_builder import ChartBuilderTool


class EvidenceExplanationSkill:
    def __init__(
        self,
        explanation_llm: StructuredLLM,
        catalog_llm: StructuredLLM,
        charts: ChartBuilderTool,
    ) -> None:
        self.explanation_llm = explanation_llm
        self.catalog_llm = catalog_llm
        self.charts = charts

    async def explain(
        self,
        *,
        question: str,
        interpretation: str,
        result: QueryResult,
        verification: VerificationOutput,
        raw_user_message: str = "",
        semantic_query_spec: dict | None = None,
        compiled_sql_artifact: dict | None = None,
    ) -> ExplanationOutput:
        system = """
You are an enterprise analytics explanation agent. Answer in the same language as the user.
Use only the supplied rows and verification notes. State the main conclusion first, then concise
findings and caveats. Do not claim causality. Do not expose hidden reasoning or sensitive data.
Return a table-oriented visualization placeholder; chart selection is applied deterministically.
""".strip()
        user = json.dumps(
            {
                "question": question,
                "raw_user_message": raw_user_message,
                "interpretation": interpretation,
                "semantic_query_spec": semantic_query_spec or {},
                "compiled_sql_artifact": compiled_sql_artifact or {},
                "columns": result.columns,
                "rows": result.rows[:100],
                "row_count": result.row_count,
                "verification": verification.model_dump(),
            },
            ensure_ascii=False,
            default=str,
        )
        output = await self.explanation_llm.parse(
            system=system,
            user=user,
            response_model=ExplanationOutput,
        )
        output.visualization = self.charts.build(result, title=output.visualization.title)
        output.caveats = verification.caveats + output.caveats
        return output

    async def answer_catalog_question(
        self,
        question: str,
        semantic_context: dict,
    ) -> CatalogAnswerOutput:
        system = """
You answer questions about a governed semantic catalog. Use only the supplied catalog context.
Explain definitions, metrics, dimensions, sources, owners, and permitted joins. If the catalog does
not contain the answer, say so. Do not generate or execute SQL.
""".strip()
        return await self.catalog_llm.parse(
            system=system,
            user=json.dumps(
                {"question": question, "semantic_context": semantic_context},
                ensure_ascii=False,
                default=str,
            ),
            response_model=CatalogAnswerOutput,
        )
