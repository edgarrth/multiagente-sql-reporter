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
        """Validate the current executable SQL candidate without double counting retries.

        A task owns one executable-query slot. SQL generation, deterministic validation and
        PostgreSQL ``EXPLAIN`` may retry that same candidate. Query slots and candidate planner
        resources therefore use replacement semantics: each retry replaces the previous candidate
        reservation instead of accumulating it. Actual executed queries continue to be counted by
        the investigation-level budget.
        """
        projected = usage.model_copy(deep=True)
        projected.queries = 1
        projected.exhausted_reasons = [
            reason
            for reason in projected.exhausted_reasons
            if reason not in {
                "max_queries",
                "max_plan_cost_total",
                "max_plan_rows_total",
                "max_relation_bytes_total",
            }
        ]
        if cost is not None:
            projected.plan_cost_total = float(cost.total_cost or 0.0)
            projected.plan_rows_total = int(cost.max_node_rows or cost.plan_rows or 0)
            projected.relation_bytes_total = int(cost.relation_bytes or 0)
        return self.evaluate(projected)

    def assert_can_continue(self, usage: TaskBudgetUsage) -> None:
        decision = self.evaluate(usage)
        if not decision.approved:
            raise TaskBudgetExceeded(
                "Task budget exhausted: " + ", ".join(decision.violations)
            )
