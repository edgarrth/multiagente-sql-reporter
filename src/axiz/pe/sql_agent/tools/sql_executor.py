"""Backward-compatible imports for code written before query-engine abstraction."""

from axiz.pe.sql_agent.query_engines.postgres import PostgresQueryEngine

PostgresQueryTool = PostgresQueryEngine

__all__ = ["PostgresQueryEngine", "PostgresQueryTool"]
