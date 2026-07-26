from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, TypeVar

import psycopg
from psycopg.rows import dict_row

from axiz.pe.sql_agent.models.contracts import CostValidation, QueryResult
from axiz.pe.sql_agent.query_engines.base import (
    QueryEngine,
    QueryEngineCapabilities,
    QueryEngineHealth,
)

T = TypeVar("T")


class PostgresQueryEngine(QueryEngine):
    def __init__(
        self,
        dsn: str,
        timeout_seconds: int,
        max_rows: int,
        max_plan_rows: int,
        max_plan_cost: float,
        max_relation_bytes: int,
        connect_timeout_seconds: int = 10,
        transient_retry_attempts: int = 2,
        transient_retry_base_seconds: float = 0.25,
    ) -> None:
        self.dsn = dsn
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self.max_plan_rows = max_plan_rows
        self.max_plan_cost = max_plan_cost
        self.max_relation_bytes = max_relation_bytes
        self.connect_timeout_seconds = connect_timeout_seconds
        self.transient_retry_attempts = max(0, transient_retry_attempts)
        self.transient_retry_base_seconds = max(0.01, transient_retry_base_seconds)
        self._capabilities = QueryEngineCapabilities(
            engine="postgres",
            dialect="postgres",
            supports_explain=True,
            supports_read_only_transactions=True,
            supports_relation_size=True,
            supports_statement_timeout=True,
            metadata={"driver": "psycopg3"},
        )

    @property
    def capabilities(self) -> QueryEngineCapabilities:
        return self._capabilities

    async def _with_transient_retry(self, operation: Callable[[], Awaitable[T]]) -> T:
        last_error: Exception | None = None
        for attempt in range(self.transient_retry_attempts + 1):
            try:
                return await operation()
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                last_error = exc
                if attempt >= self.transient_retry_attempts:
                    raise
                await asyncio.sleep(self.transient_retry_base_seconds * (2**attempt))
        assert last_error is not None
        raise last_error

    async def health(self) -> QueryEngineHealth:
        started = time.perf_counter()

        async def operation() -> bool:
            async with await psycopg.AsyncConnection.connect(
                self.dsn, connect_timeout=self.connect_timeout_seconds
            ) as conn:
                row = await (await conn.execute("SELECT 1")).fetchone()
                return bool(row and row[0] == 1)

        try:
            healthy = await self._with_transient_retry(operation)
            return QueryEngineHealth(
                healthy=healthy,
                engine=self.capabilities.engine,
                dialect=self.capabilities.dialect,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except Exception as exc:
            return QueryEngineHealth(
                healthy=False,
                engine=self.capabilities.engine,
                dialect=self.capabilities.dialect,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                message=str(exc),
            )

    async def estimate_cost(self, sql: str, tables: list[str]) -> CostValidation:
        async def operation() -> CostValidation:
            warnings: list[str] = []
            async with await psycopg.AsyncConnection.connect(
                self.dsn,
                row_factory=dict_row,
                connect_timeout=self.connect_timeout_seconds,
            ) as conn:
                await conn.execute("SET default_transaction_read_only = on")
                await conn.execute(f"SET statement_timeout = '{self.timeout_seconds}s'")
                explain_row = (
                    await (await conn.execute(f"EXPLAIN (FORMAT JSON) {sql}")).fetchone()
                )
                explain_value = next(iter(explain_row.values())) if explain_row else []
                plan = explain_value[0]["Plan"] if explain_value else {}
                total_cost = float(plan.get("Total Cost", 0))
                plan_rows = int(plan.get("Plan Rows", 0))
                plan_width = int(plan.get("Plan Width", 0))
                plan_nodes = list(self._plan_nodes(plan))
                max_node_rows = max(
                    (int(node.get("Plan Rows", 0) or 0) for node in plan_nodes),
                    default=plan_rows,
                )

                plan_relations = sorted(self._plan_relations(explain_value))
                relations_to_size = plan_relations or tables
                relation_bytes = 0
                for table in relations_to_size:
                    row = await (
                        await conn.execute(
                            "SELECT COALESCE(pg_total_relation_size(to_regclass(%s)), 0) AS bytes",
                            (table,),
                        )
                    ).fetchone()
                    relation_bytes += int(row["bytes"] if row else 0)

            if total_cost > self.max_plan_cost:
                warnings.append(
                    f"Planner cost {total_cost:.2f} exceeds limit {self.max_plan_cost:.2f}"
                )
            if max_node_rows > self.max_plan_rows:
                warnings.append(
                    f"Maximum node estimated rows {max_node_rows} exceed limit "
                    f"{self.max_plan_rows}"
                )
            if relation_bytes > self.max_relation_bytes:
                warnings.append(
                    f"Referenced relation size {relation_bytes} bytes exceeds limit "
                    f"{self.max_relation_bytes}"
                )
            return CostValidation(
                approved=not warnings,
                engine=self.capabilities.engine,
                dialect=self.capabilities.dialect,
                total_cost=total_cost,
                plan_rows=plan_rows,
                plan_width=plan_width,
                max_node_rows=max_node_rows,
                plan_node_count=len(plan_nodes),
                relation_bytes=relation_bytes,
                warnings=warnings,
                explain_plan=explain_value,
                tables=tables,
                plan_relations=plan_relations,
                max_plan_cost=self.max_plan_cost,
                max_plan_rows=self.max_plan_rows,
                max_relation_bytes=self.max_relation_bytes,
                timeout_seconds=self.timeout_seconds,
            )

        return await self._with_transient_retry(operation)

    @classmethod
    def _plan_nodes(cls, node: Any) -> Iterator[dict[str, Any]]:
        if isinstance(node, dict):
            if node.get("Node Type"):
                yield node
            for child in node.get("Plans", []) or []:
                yield from cls._plan_nodes(child)
        elif isinstance(node, list):
            for child in node:
                yield from cls._plan_nodes(child)

    @classmethod
    def _plan_relations(cls, node: Any) -> set[str]:
        relations: set[str] = set()
        if isinstance(node, dict):
            relation = node.get("Relation Name")
            schema = node.get("Schema")
            if relation:
                relations.add(f"{schema}.{relation}" if schema else str(relation))
            for value in node.values():
                relations.update(cls._plan_relations(value))
        elif isinstance(node, list):
            for value in node:
                relations.update(cls._plan_relations(value))
        return relations

    async def execute(self, sql: str) -> QueryResult:
        started = time.perf_counter()

        async def operation() -> QueryResult:
            async with await psycopg.AsyncConnection.connect(
                self.dsn,
                row_factory=dict_row,
                connect_timeout=self.connect_timeout_seconds,
            ) as conn:
                await conn.execute("BEGIN READ ONLY")
                await conn.execute(
                    f"SET LOCAL statement_timeout = '{self.timeout_seconds}s'"
                )
                cursor = await conn.execute(sql)
                rows = await cursor.fetchmany(self.max_rows + 1)
                await conn.rollback()

            truncated = len(rows) > self.max_rows
            bounded_rows = rows[: self.max_rows]
            elapsed_ms = (time.perf_counter() - started) * 1000
            columns = list(bounded_rows[0].keys()) if bounded_rows else []
            return QueryResult(
                engine=self.capabilities.engine,
                dialect=self.capabilities.dialect,
                columns=columns,
                rows=[dict(row) for row in bounded_rows],
                row_count=len(bounded_rows),
                elapsed_ms=round(elapsed_ms, 2),
                truncated=truncated,
            )

        return await self._with_transient_retry(operation)
