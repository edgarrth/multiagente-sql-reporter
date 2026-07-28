from pathlib import Path

import pytest

from axiz.pe.sql_agent.models.contracts import ContextRelation, ConversationMemory


ROOT = Path(__file__).resolve().parents[2]


def test_sql_generation_is_open_ended_and_has_no_fixed_clause_checklist() -> None:
    source = (ROOT / "src/axiz/pe/sql_agent/skills/sql/generation.py").read_text(
        encoding="utf-8"
    )
    assert "Do not require a fixed" in source
    assert "full previous SQL" in source
    assert "closed list of feedback types" in source
    assert "Always bound transaction data by date" not in source
    assert "required_filter_columns" not in source


def test_context_resolver_routes_first_request_without_inventing_parameters() -> None:
    pytest.importorskip("structlog")
    from axiz.pe.sql_agent.skills.coordinator.context_resolution import ContextResolutionSkill

    class NeverCalledLlm:
        async def parse(self, **kwargs):  # pragma: no cover
            raise AssertionError("A first standalone request must not need dependency resolution")

    async def run():
        question = "Dame las 20 últimas transacciones ejecutadas"
        output = await ContextResolutionSkill(NeverCalledLlm()).resolve(
            question=question,
            memory=ConversationMemory(),
            history=[{"role": "user", "content": question}],
        )
        assert output.relation == ContextRelation.INDEPENDENT_REQUEST
        assert output.resolved_question == question
        assert output.requires_clarification is False

    import asyncio

    asyncio.run(run())


def test_catalog_has_no_mandatory_query_shape_policy() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "semantic_catalog/domains/acquiring/domain.yaml",
            ROOT / "semantic_catalog/global/calendars.yaml",
        )
    )
    for forbidden in (
        "required_filter_columns",
        "enforce_temporal_filter",
        "allow_ordered_top_n_without_time_filter",
        "temporal_filter_columns",
        "default_date_column",
        "Prefer closed calendar periods",
        "Include an explicit lower and upper date boundary for transactional views",
    ):
        assert forbidden not in sources


def test_clarification_is_generic_not_temporal_hardcode() -> None:
    source = (ROOT / "src/axiz/pe/sql_agent/workflow/nodes.py").read_text(
        encoding="utf-8"
    )
    assert "el cambio temporal es ambiguo" not in source
    assert "No existe una consulta analítica anterior" not in source
