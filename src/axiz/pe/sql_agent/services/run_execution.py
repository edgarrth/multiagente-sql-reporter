from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from uuid import UUID

from axiz.pe.sql_agent.repositories.run_repository import RunRepository


class RunExecutionCoordinator:
    """Maintains a distributed DB lease and observes cancellation requests."""

    def __init__(
        self,
        runs: RunRepository,
        *,
        lease_seconds: int,
        heartbeat_seconds: int,
    ) -> None:
        self.runs = runs
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = max(1, min(heartbeat_seconds, max(1, lease_seconds // 2)))

    @asynccontextmanager
    async def execution(self, run_id: UUID, lease_owner: str) -> AsyncIterator[None]:
        stop = asyncio.Event()
        parent = asyncio.current_task()

        async def heartbeat_loop() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_seconds)
                    break
                except TimeoutError:
                    pass
                if await self.runs.is_cancel_requested(run_id, lease_owner):
                    if parent is not None:
                        parent.cancel()
                    return
                owned = await self.runs.heartbeat(
                    run_id, lease_owner, self.lease_seconds
                )
                if not owned:
                    if parent is not None:
                        parent.cancel()
                    return

        task = asyncio.create_task(heartbeat_loop(), name=f"run-heartbeat-{run_id}")
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
