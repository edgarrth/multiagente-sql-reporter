from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from axiz.pe.sql_agent.models.contracts import LLMCallUsage, LLMUsageSummary


_CURRENT_USAGE: ContextVar["LLMUsageCollector | None"] = ContextVar(
    "axiz_llm_usage_collector",
    default=None,
)


class LLMUsageCollector:
    """Collects provider token usage for one logical agent run.

    The collector is context-local so concurrent user runs do not mix metrics. A mutable
    collector instance is intentionally stored in the ContextVar; asyncio tasks created by
    LangGraph inherit the same instance and append to the same run-level collection.
    """

    def __init__(self, existing: LLMUsageSummary | dict[str, Any] | None = None) -> None:
        if isinstance(existing, LLMUsageSummary):
            self.calls = list(existing.calls)
        elif isinstance(existing, dict):
            self.calls = list(LLMUsageSummary.model_validate(existing).calls)
        else:
            self.calls: list[LLMCallUsage] = []

    def record(self, usage: LLMCallUsage) -> None:
        self.calls.append(usage)

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
) -> tuple[LLMUsageCollector, Token]:
    collector = LLMUsageCollector(existing)
    token = _CURRENT_USAGE.set(collector)
    return collector, token


def reset_llm_usage_collection(token: Token) -> None:
    _CURRENT_USAGE.reset(token)


def current_llm_usage_collector() -> LLMUsageCollector | None:
    return _CURRENT_USAGE.get()
