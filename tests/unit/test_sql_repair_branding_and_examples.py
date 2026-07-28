from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def test_latest_transactions_contract_uses_real_timestamp_and_valid_statuses() -> None:
    entity = yaml.safe_load(
        (
            ROOT
            / "semantic_catalog"
            / "domains"
            / "acquiring"
            / "entities"
            / "payment_transaction.yaml"
        ).read_text(encoding="utf-8")
    )
    dimensions = {item["name"]: item for item in entity["dimensions"]}

    assert dimensions["transaction_timestamp"]["column"] == "transaction_timestamp"
    assert dimensions["status"]["allowed_values"] == ["APPROVED", "DECLINED", "REVERSED"]

    semantic_sql = (
        ROOT / "infrastructure" / "postgres" / "init" / "04-analytics-semantic.sql"
    ).read_text(encoding="utf-8")
    assert "f.transaction_ts AS transaction_timestamp" in semantic_sql
    assert "idx_fact_payment_transaction_ts" in semantic_sql



def test_semantic_catalog_publishes_entity_dimensions() -> None:
    from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool

    catalog = SemanticCatalogTool(ROOT / "semantic_catalog")
    symbols = catalog.semantic_symbols("acquiring")
    dimension_names = {item.get("name") for item in symbols["dimensions"]}
    source_names = {item.get("source") for item in symbols["sources"]}

    assert "transaction_timestamp" in dimension_names
    assert "status" in dimension_names
    assert "semantic.v_payment_transactions" in source_names

def test_latest_transactions_example_does_not_invent_execution_fields() -> None:
    examples = yaml.safe_load(
        (
            ROOT
            / "semantic_catalog"
            / "domains"
            / "acquiring"
            / "examples"
            / "questions.yaml"
        ).read_text(encoding="utf-8")
    )["examples"]
    example = next(item for item in examples if item["id"] == "ACQ-008")
    sql = example["sql"]

    assert "ORDER BY transaction_timestamp DESC" in sql
    assert "LIMIT 20" in sql
    assert "execution_timestamp" not in sql
    assert "status = 'EXECUTED'" not in sql


def test_readme_contains_at_least_ten_agent_query_examples() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("# Ejemplos de consultas para el agente", 1)[1].split(
        "# Configuración de optimización", 1
    )[0]
    numbered_queries = re.findall(r"^\d+\. `.+`$", section, flags=re.MULTILINE)

    assert len(numbered_queries) >= 10
    assert "Dame las 20 últimas transacciones ejecutadas." in section


def test_brand_assets_are_vector_backed_and_high_resolution() -> None:
    assets = ROOT / "streamlit_app" / "assets"
    assert (assets / "axiz-logo.svg").exists()
    assert (assets / "axiz-agent-icon.svg").exists()
    assert (assets / "axiz-logo-generated-source.png").exists()

    icon = Image.open(assets / "axiz-agent-icon.png")
    favicon = Image.open(assets / "favicon.png")
    logo = Image.open(assets / "axiz-logo@2x.png")

    assert icon.size == (512, 512)
    assert favicon.size == (512, 512)
    assert logo.width >= 1200
    assert logo.height >= 400


@pytest.mark.asyncio
async def test_postgres_planner_error_becomes_retryable_validation(monkeypatch) -> None:
    psycopg = pytest.importorskip("psycopg")
    from axiz.pe.sql_agent.query_engines.postgres import PostgresQueryEngine

    engine = PostgresQueryEngine(
        dsn="postgresql://reader:pwd@localhost:5432/business",
        timeout_seconds=30,
        max_rows=500,
        max_plan_rows=1_000_000,
        max_plan_cost=1_000_000.0,
        max_relation_bytes=1_000_000_000,
    )

    async def raise_undefined_column(_operation):
        raise psycopg.errors.UndefinedColumn(
            'column "execution_timestamp" does not exist'
        )

    monkeypatch.setattr(engine, "_with_transient_retry", raise_undefined_column)
    result = await engine.estimate_cost(
        "SELECT * FROM semantic.v_payment_transactions ORDER BY execution_timestamp",
        ["semantic.v_payment_transactions"],
    )

    assert result.approved is False
    assert result.failure_type == "sql_validation"
    assert result.error_code == "42703"
    assert "execution_timestamp" in (result.error_message or "")
    assert "exact catalog columns" in result.warnings[0]

@pytest.mark.asyncio
async def test_sql_generator_receives_failed_sql_and_exact_catalog_guidance() -> None:
    import json

    from axiz.pe.sql_agent.skills.sql.generation import SqlGenerationSkill
    from axiz.pe.sql_agent.models.contracts import SqlGenerationOutput

    class CapturingLLM:
        def __init__(self) -> None:
            self.system = ""
            self.user = ""

        async def parse(self, *, system, user, response_model):
            self.system = system
            self.user = user
            assert response_model is SqlGenerationOutput
            return SqlGenerationOutput(
                sql=(
                    "SELECT transaction_id FROM semantic.v_payment_transactions "
                    "WHERE transaction_date <= CURRENT_DATE "
                    "ORDER BY transaction_timestamp DESC LIMIT 20"
                ),
                interpretation="Últimas transacciones",
            )

    llm = CapturingLLM()
    agent = SqlGenerationSkill(llm=llm, dialect="postgres", max_result_rows=500)
    failed_sql = (
        "SELECT * FROM semantic.v_payment_transactions "
        "ORDER BY execution_timestamp DESC LIMIT 20"
    )
    await agent.generate(
        question="Dame las 20 últimas transacciones ejecutadas",
        semantic_context={"allowed_sources": ["semantic.v_payment_transactions"]},
        history=[],
        prior_compliance={
            "retry_instruction": "column execution_timestamp does not exist",
            "failed_sql": failed_sql,
        },
    )

    payload = json.loads(llm.user)
    assert payload["prior_compliance"]["failed_sql"] == failed_sql
    assert "Do not fabricate columns" in llm.system
    assert "exact available identifiers" in llm.system
    assert "failed SQL is not an approved baseline" in llm.system
