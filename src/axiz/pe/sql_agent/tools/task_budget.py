from __future__ import annotations

from dataclasses import dataclass

from axiz.pe.sql_agent.models.contracts import (
    CostValidation,
    TaskBudget,
    TaskBudgetUsage,
)


class TaskBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskBudgetDecision:
    approved: bool
    usage: TaskBudgetUsage
    violations: list[str]


class TaskBudgetPolicy:
    """Deterministic per-task policy. LLM output cannot modify these limits."""

    def __init__(self, budget: TaskBudget) -> None:
        self.budget = budget

    def evaluate(
        self,
        usage: TaskBudgetUsage,
        *,
        cost: CostValidation | None = None,
        additional_llm_tokens: int = 0,
        additional_active_seconds: float = 0.0,
        additional_queries: int = 0,
    ) -> TaskBudgetDecision:
        projected = usage.model_copy(deep=True)
        projected.llm_tokens += max(0, int(additional_llm_tokens))
        projected.active_seconds += max(0.0, float(additional_active_seconds))
        projected.queries += max(0, int(additional_queries))
        if cost is not None:
            projected.plan_cost_total += float(cost.total_cost or 0.0)
            projected.plan_rows_total += int(cost.max_node_rows or cost.plan_rows or 0)
            projected.relation_bytes_total += int(cost.relation_bytes or 0)

        violations: list[str] = []
        if projected.attempts > self.budget.max_attempts:
            violations.append("max_attempts")
        if projected.replans > self.budget.max_replans:
            violations.append("max_replans")
        if projected.llm_tokens > self.budget.max_llm_tokens:
            violations.append("max_llm_tokens")
        if projected.queries > self.budget.max_queries:
            violations.append("max_queries")
        if projected.active_seconds > self.budget.max_active_seconds:
            violations.append("max_active_seconds")
        if projected.plan_cost_total > self.budget.max_plan_cost_total:
            violations.append("max_plan_cost_total")
        if projected.plan_rows_total > self.budget.max_plan_rows_total:
            violations.append("max_plan_rows_total")
        if projected.relation_bytes_total > self.budget.max_relation_bytes_total:
            violations.append("max_relation_bytes_total")
        projected.exhausted_reasons = sorted(set(projected.exhausted_reasons + violations))
        return TaskBudgetDecision(
            approved=not violations,
            usage=projected,
            violations=violations,
        )

    def evaluate_query_proposal(
        self,
        usage: TaskBudgetUsage,
        *,
        cost: CostValidation | None = None,
    ) -> TaskBudgetDecision:
        """Reserve one executable-query slot for a task, idempotently.

        SQL generation, security validation and EXPLAIN may retry the same proposal. Those retries
        are governed by ``max_attempts`` and cumulative cost limits; they must not be counted as
        additional business-query executions. A task reserves its single execution slot once.
        """
        normalized_usage = usage.model_copy(deep=True)
        # Versions prior to 0.9.6 counted every EXPLAIN/repair as a new query. Migrate that
        # checkpoint-local counter to the current one-slot-per-task meaning without changing any
        # global execution counters.
        if normalized_usage.queries > 1:
            normalized_usage.queries = 1
            normalized_usage.exhausted_reasons = [
                reason
                for reason in normalized_usage.exhausted_reasons
                if reason != "max_queries"
            ]
        additional_query_slots = 0 if normalized_usage.queries >= 1 else 1
        return self.evaluate(
            normalized_usage,
            cost=cost,
            additional_queries=additional_query_slots,
        )

    def assert_can_continue(self, usage: TaskBudgetUsage) -> None:
        decision = self.evaluate(usage)
        if not decision.approved:
            raise TaskBudgetExceeded(
                "Task budget exhausted: " + ", ".join(decision.violations)
            )
