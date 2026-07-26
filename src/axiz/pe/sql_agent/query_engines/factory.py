from __future__ import annotations

from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.query_engines.base import QueryEngine


class QueryEngineConfigurationError(RuntimeError):
    pass


class QueryEngineFactory:
    @staticmethod
    def create(settings: Settings) -> QueryEngine:
        if settings.query_engine == "postgres":
            # Lazy import keeps configuration and documentation tooling usable before
            # provider-specific drivers are installed.
            from axiz.pe.sql_agent.query_engines.postgres import PostgresQueryEngine

            return PostgresQueryEngine(
                settings.agent_database_url.get_secret_value(),
                timeout_seconds=settings.sql_timeout_seconds,
                max_rows=settings.max_result_rows,
                max_plan_rows=settings.max_plan_rows,
                max_plan_cost=settings.max_plan_cost,
                max_relation_bytes=settings.max_relation_bytes,
                connect_timeout_seconds=settings.agent_database_connect_timeout_seconds,
                transient_retry_attempts=settings.query_engine_retry_attempts,
                transient_retry_base_seconds=settings.query_engine_retry_base_seconds,
            )
        raise QueryEngineConfigurationError(
            f"Unsupported QUERY_ENGINE={settings.query_engine!r}. "
            "Register an implementation in QueryEngineFactory."
        )
