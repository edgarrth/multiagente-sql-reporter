from pathlib import Path

from axiz.pe.sql_agent.tools.example_selector import ExampleSelectorTool
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool


def test_catalog_discovers_domains_without_code_changes() -> None:
    catalog = SemanticCatalogTool(Path("semantic_catalog"))
    domains = catalog.list_domains()
    assert [domain["name"] for domain in domains] == ["acquiring"]
    assert "semantic.v_daily_payment_metrics" in catalog.allowed_sources("acquiring")
    assert "semantic.v_chargeback_metrics" in catalog.allowed_sources("acquiring")
    assert len(catalog.allowed_sources("acquiring")) == 6


def test_catalog_selects_relevant_examples() -> None:
    catalog = SemanticCatalogTool(Path("semantic_catalog"))
    selector = ExampleSelectorTool(catalog)
    examples = selector.select("tasa de aprobación por canal", "acquiring")
    assert examples
    assert "approval_rate" in examples[0].get("sql", "")
