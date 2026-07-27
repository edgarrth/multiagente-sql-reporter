from pathlib import Path

from axiz.pe.sql_agent.tools.example_selector import ExampleSelectorTool
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool


def test_catalog_discovers_domains_without_code_changes() -> None:
    catalog = SemanticCatalogTool(Path("semantic_catalog"))
    domains = catalog.list_domains()
    assert [domain["name"] for domain in domains] == ["acquiring"]
    assert "semantic.v_daily_payment_metrics" in catalog.allowed_sources("acquiring")
    assert "semantic.v_chargeback_metrics" in catalog.allowed_sources("acquiring")
    assert len(catalog.allowed_sources("acquiring")) == 7
    assert "semantic.v_merchant_settlement_metrics" in catalog.allowed_sources("acquiring")


def test_catalog_selects_relevant_examples() -> None:
    catalog = SemanticCatalogTool(Path("semantic_catalog"))
    selector = ExampleSelectorTool(catalog)
    examples = selector.select("tasa de aprobación por canal", "acquiring")
    assert examples
    assert "approval_rate" in examples[0].get("sql", "")


def test_declines_yesterday_example_uses_certified_measure_and_lima_bounds() -> None:
    catalog = SemanticCatalogTool(Path("semantic_catalog"))
    selector = ExampleSelectorTool(catalog)
    examples = selector.select(
        "¿Cuántas transacciones fueron rechazadas ayer por código de respuesta?",
        "acquiring",
    )
    sql = "\n".join(str(example.get("sql") or "") for example in examples)

    assert "semantic.v_decline_analysis" in sql
    assert "SUM(declined_count)" in sql
    assert "TIMEZONE('America/Lima', CURRENT_TIMESTAMP)" in sql
    assert "COUNT(*)" not in sql


def test_settlement_failure_example_prefers_certified_aggregate_view() -> None:
    catalog = SemanticCatalogTool(Path("semantic_catalog"))
    selector = ExampleSelectorTool(catalog)
    examples = selector.select(
        "Lista los comercios con más fallas de liquidación durante los últimos 30 días",
        "acquiring",
    )
    sql = "\n".join(str(example.get("sql") or "") for example in examples)

    assert "semantic.v_merchant_settlement_metrics" in sql
    assert "SUM(failed_settlement_count)" in sql
