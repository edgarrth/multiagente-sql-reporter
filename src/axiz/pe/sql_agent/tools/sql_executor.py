from __future__ import annotations

import time
from typing import Any

import psycopg
from psycopg.rows import dict_row

from axiz.pe.sql_agent.models.contracts import CostValidation, QueryResult


class PostgresQueryTool:
    def __init__(
        self,
        dsn: str,
        timeout_seconds: int,
        max_rows: int,
        max_plan_rows: int,
        max_plan_cost: float,
        max_relation_bytes: int,
    ) -> None:
        self.dsn = dsn
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self.max_plan_rows = max_plan_rows
        self.max_plan_cost = max_plan_cost
        self.max_relation_bytes = max_relation_bytes

    async def estimate_cost(self, sql: str, tables: list[str]) -> CostValidation:
        warnings: list[str] = []
        async with await psycopg.AsyncConnection.connect(self.dsn, row_factory=dict_row) as conn:
            await conn.execute("SET default_transaction_read_only = on")
            await conn.execute(f"SET statement_timeout = '{self.timeout_seconds}s'")
            explain_row = (
                await (await conn.execute(f"EXPLAIN (FORMAT JSON) {sql}")).fetchone()
            )
            explain_value = next(iter(explain_row.values())) if explain_row else []
            plan = explain_value[0]["Plan"] if explain_value else {}
            total_cost = float(plan.get("Total Cost", 0))
            plan_rows = int(plan.get("Plan Rows", 0))

            relation_bytes = 0
            for table in tables:
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
        if plan_rows > self.max_plan_rows:
            warnings.append(f"Estimated rows {plan_rows} exceed limit {self.max_plan_rows}")
        if relation_bytes > self.max_relation_bytes:
            warnings.append(
                f"Referenced relation size {relation_bytes} bytes exceeds limit "
                f"{self.max_relation_bytes}"
            )
        return CostValidation(
            approved=not warnings,
            total_cost=total_cost,
            plan_rows=plan_rows,
            relation_bytes=relation_bytes,
            warnings=warnings,
            explain_plan=explain_value,
        )

    async def execute(self, sql: str) -> QueryResult:
        started = time.perf_counter()
        async with await psycopg.AsyncConnection.connect(self.dsn, row_factory=dict_row) as conn:
            await conn.execute("BEGIN READ ONLY")
            await conn.execute(f"SET LOCAL statement_timeout = '{self.timeout_seconds}s'")
            cursor = await conn.execute(sql)
            rows = await cursor.fetchmany(self.max_rows + 1)
            await conn.rollback()

        truncated = len(rows) > self.max_rows
        rows = rows[: self.max_rows]
        elapsed_ms = (time.perf_counter() - started) * 1000
        columns = list(rows[0].keys()) if rows else []
        return QueryResult(
            columns=columns,
            rows=[dict(row) for row in rows],
            row_count=len(rows),
            elapsed_ms=round(elapsed_ms, 2),
            truncated=truncated,
        )
