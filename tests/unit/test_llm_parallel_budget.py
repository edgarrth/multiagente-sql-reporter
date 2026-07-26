import asyncio

import pytest

from axiz.pe.sql_agent.services.llm_usage import LLMRunBudgetExceeded, LLMUsageCollector


@pytest.mark.asyncio
async def test_parallel_llm_reservations_cannot_overbook_run_budget() -> None:
    collector = LLMUsageCollector(max_total_tokens=100)

    async def reserve(call_id: str):
        await collector.reserve(call_id, 60, agent=call_id)
        return call_id

    results = await asyncio.gather(
        reserve("call-a"), reserve("call-b"), return_exceptions=True
    )
    assert sum(isinstance(item, LLMRunBudgetExceeded) for item in results) == 1
    assert sum(item in {"call-a", "call-b"} for item in results if isinstance(item, str)) == 1

    await collector.release("call-a")
    await collector.release("call-b")
    await collector.reserve("call-c", 100, agent="call-c")


@pytest.mark.asyncio
async def test_parallel_calls_cannot_overbook_per_task_token_budget() -> None:
    collector = LLMUsageCollector(max_total_tokens=1000)

    async def reserve(call_id: str):
        await collector.reserve(
            call_id,
            60,
            agent=call_id,
            scope_id="task-1",
            scope_limit_tokens=100,
        )
        return call_id

    results = await asyncio.gather(
        reserve("call-a"), reserve("call-b"), return_exceptions=True
    )
    assert sum(isinstance(item, LLMRunBudgetExceeded) for item in results) == 1
