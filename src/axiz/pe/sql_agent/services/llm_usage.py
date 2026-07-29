from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from axiz.pe.sql_agent.models.contracts import LLMCallUsage, LLMUsageSummary


_CURRENT_USAGE: ContextVar["LLMUsageCollector | None"] = ContextVar(
    "axiz_llm_usage_collector",
    default=None,
)

_CURRENT_USAGE_SCOPE: ContextVar[str | None] = ContextVar(
    "axiz_llm_usage_scope", default=None
)
_CURRENT_SPECIALIST: ContextVar[str | None] = ContextVar(
    "axiz_llm_usage_specialist", default=None
)
_CURRENT_SCOPE_TOKEN_LIMIT: ContextVar[int | None] = ContextVar(
    "axiz_llm_usage_scope_token_limit", default=None
)


class LLMRunBudgetExceeded(RuntimeError):
    pass


class LLMUsageCollector:
    """Collects provider token usage for one logical agent run.

    The collector is context-local so concurrent user runs do not mix metrics. A mutable
    collector instance is intentionally stored in the ContextVar; asyncio tasks created by
    LangGraph inherit the same instance and append to the same run-level collection.
    """

    def __init__(
        self,
        existing: LLMUsageSummary | dict[str, Any] | None = None,
        *,
        max_total_tokens: int | None = None,
    ) -> None:
        if isinstance(existing, LLMUsageSummary):
            self.calls = list(existing.calls)
        elif isinstance(existing, dict):
            self.calls = list(LLMUsageSummary.model_validate(existing).calls)
        else:
            self.calls: list[LLMCallUsage] = []
        self.max_total_tokens = max_total_tokens
        self._reservation_lock = asyncio.Lock()
        self._reservations: dict[str, tuple[int, str | None]] = {}

    def budget_consumed(self) -> int:
        return sum(
            call.total_tokens
            if call.total_tokens is not None
            else call.estimated_max_total_tokens
            for call in self.calls
        )

    def assert_can_reserve(self, requested_tokens: int, *, agent: str) -> None:
        """Sequential compatibility check.

        Runtime LLM calls use :meth:`reserve` so concurrent specialist fan-out cannot overbook the
        shared run budget.
        """
        if self.max_total_tokens is None:
            return
        projected = self.budget_consumed() + sum(value[0] for value in self._reservations.values()) + requested_tokens
        if projected > self.max_total_tokens:
            raise LLMRunBudgetExceeded(
                f"La llamada del agente {agent} excedería el presupuesto LLM del run: "
                f"{projected} > {self.max_total_tokens} tokens reservados"
            )

    async def reserve(
        self,
        call_id: str,
        requested_tokens: int,
        *,
        agent: str,
        scope_id: str | None = None,
        scope_limit_tokens: int | None = None,
    ) -> None:
        async with self._reservation_lock:
            self.assert_can_reserve(requested_tokens, agent=agent)
            if scope_id and scope_limit_tokens is not None:
                consumed = self.tokens_for_scope(scope_id)
                reserved = sum(
                    tokens
                    for tokens, reserved_scope in self._reservations.values()
                    if reserved_scope == scope_id
                )
                projected = consumed + reserved + requested_tokens
                if projected > scope_limit_tokens:
                    remaining = max(0, scope_limit_tokens - consumed - reserved)
                    raise LLMRunBudgetExceeded(
                        f"La llamada del agente {agent} excedería el presupuesto LLM "
                        f"de la tarea {scope_id}: {projected} > {scope_limit_tokens} tokens "
                        f"(consumidos={consumed}, reservados={reserved}, "
                        f"solicitud={requested_tokens}, disponibles={remaining})"
                    )
            self._reservations[call_id] = (requested_tokens, scope_id)

    async def settle(self, usage: LLMCallUsage) -> None:
        async with self._reservation_lock:
            self._reservations.pop(usage.call_id, None)
            self.calls.append(usage)

    async def release(self, call_id: str) -> None:
        async with self._reservation_lock:
            self._reservations.pop(call_id, None)

    def record(self, usage: LLMCallUsage) -> None:
        """Compatibility method for deterministic tests outside parallel execution."""
        self._reservations.pop(usage.call_id, None)
        self.calls.append(usage)

    def tokens_for_scope(self, scope_id: str) -> int:
        return sum(
            call.total_tokens
            if call.total_tokens is not None
            else call.estimated_max_total_tokens
            for call in self.calls
            if call.scope_id == scope_id
        )

    def summary(self) -> LLMUsageSummary:
        completed = [call for call in self.calls if call.status == "completed"]
        failed = [call for call in self.calls if call.status == "failed"]
        actual_complete = all(
            call.total_tokens is not None
            for call in completed
        )
        return LLMUsageSummary(
            call_count=len(self.calls),
            completed_calls=len(completed),
            failed_calls=len(failed),
            estimated_input_tokens=sum(call.estimated_input_tokens for call in self.calls),
            reserved_output_tokens=sum(call.reserved_output_tokens for call in self.calls),
            estimated_max_total_tokens=sum(
                call.estimated_max_total_tokens for call in self.calls
            ),
            actual_input_tokens=sum(call.input_tokens or 0 for call in completed),
            actual_output_tokens=sum(call.output_tokens or 0 for call in completed),
            actual_total_tokens=sum(call.total_tokens or 0 for call in completed),
            cached_input_tokens=sum(call.cached_input_tokens for call in completed),
            reasoning_output_tokens=sum(call.reasoning_output_tokens for call in completed),
            actual_usage_complete=actual_complete,
            calls=list(self.calls),
        )


def activate_llm_usage_collection(
    existing: LLMUsageSummary | dict[str, Any] | None = None,
    *,
    max_total_tokens: int | None = None,
) -> tuple[LLMUsageCollector, Token]:
    collector = LLMUsageCollector(existing, max_total_tokens=max_total_tokens)
    token = _CURRENT_USAGE.set(collector)
    return collector, token


def reset_llm_usage_collection(token: Token) -> None:
    _CURRENT_USAGE.reset(token)


def current_llm_usage_collector() -> LLMUsageCollector | None:
    return _CURRENT_USAGE.get()


def current_llm_usage_scope() -> tuple[str | None, str | None]:
    return _CURRENT_USAGE_SCOPE.get(), _CURRENT_SPECIALIST.get()


def current_llm_scope_token_limit() -> int | None:
    return _CURRENT_SCOPE_TOKEN_LIMIT.get()


@contextmanager
def llm_usage_scope(
    scope_id: str,
    specialist_id: str | None = None,
    *,
    max_tokens: int | None = None,
):
    scope_token = _CURRENT_USAGE_SCOPE.set(scope_id)
    specialist_token = _CURRENT_SPECIALIST.set(specialist_id)
    limit_token = _CURRENT_SCOPE_TOKEN_LIMIT.set(max_tokens)
    try:
        yield
    finally:
        _CURRENT_SCOPE_TOKEN_LIMIT.reset(limit_token)
        _CURRENT_SPECIALIST.reset(specialist_token)
        _CURRENT_USAGE_SCOPE.reset(scope_token)
