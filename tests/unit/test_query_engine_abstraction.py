import pytest

pytest.importorskip("psycopg")

from pydantic import SecretStr

from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.query_engines.base import QueryEngine
from axiz.pe.sql_agent.query_engines.factory import QueryEngineFactory
from axiz.pe.sql_agent.query_engines.postgres import PostgresQueryEngine
from axiz.pe.sql_agent.tools.sql_executor import PostgresQueryTool


def test_factory_builds_postgres_through_provider_neutral_contract() -> None:
    settings = Settings(
        query_engine="postgres",
        agent_database_url=SecretStr("postgresql://reader:pwd@localhost:5432/business"),
    )
    engine = QueryEngineFactory.create(settings)

    assert isinstance(engine, QueryEngine)
    assert isinstance(engine, PostgresQueryEngine)
    assert engine.capabilities.engine == "postgres"
    assert engine.capabilities.dialect == "postgres"
    assert engine.capabilities.supports_explain is True
    assert engine.capabilities.supports_read_only_transactions is True


def test_legacy_query_tool_name_is_only_a_compatibility_alias() -> None:
    assert PostgresQueryTool is PostgresQueryEngine
