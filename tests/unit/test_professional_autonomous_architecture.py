from pathlib import Path

import pytest

from axiz.pe.sql_agent.models.contracts import (
    AutonomousBudget,
    AutonomousBudgetUsage,
    InvestigationPlan,
    InvestigationTask,
    SupervisorAction,
    SupervisorDecision,
    TaskBudget,
    TaskBudgetUsage,
    CostValidation,
)
from axiz.pe.sql_agent.services.agent_cache import AgentResponseCache, InMemoryJsonCache
from axiz.pe.sql_agent.services.specialist_registry import SpecialistRegistry
from axiz.pe.sql_agent.tools.investigation_governance import (
    InvestigationGovernanceError,
    InvestigationGovernancePolicy,
)
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool
from axiz.pe.sql_agent.tools.task_budget import TaskBudgetPolicy

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_agent_cache_uses_hash_keys_and_round_trips_json() -> None:
    cache = AgentResponseCache(InMemoryJsonCache(), namespace="test")
    payload = {"question": "sensitive prompt", "catalog": ["a", "b"]}
    key = await cache.set("plan", payload, {"tasks": ["x"]}, ttl_seconds=60)
    assert "sensitive prompt" not in key
    found = await cache.get("plan", payload)
    assert found.hit is True
    assert found.value == {"tasks": ["x"]}


def test_per_task_budget_fails_closed() -> None:
    budget = TaskBudget(max_attempts=2, max_replans=1, max_llm_tokens=100, max_queries=1)
    decision = TaskBudgetPolicy(budget).evaluate(
        TaskBudgetUsage(attempts=2, llm_tokens=90), additional_llm_tokens=11
    )
    assert decision.approved is False
    assert "max_llm_tokens" in decision.violations


def test_dynamic_registry_has_no_python_role_switch() -> None:
    registry = SpecialistRegistry(
        ROOT / "config/specialists.yaml",
        SemanticCatalogTool(ROOT / "semantic_catalog"),
    )
    assert "acquiring" in registry.enabled_roles()
    assert all(profile.graph_node_name.startswith("specialist__") for profile in registry.executable_profiles())
    source = (ROOT / "src/axiz/pe/sql_agent/workflow/graph.py").read_text(encoding="utf-8")
    assert "specialist_graph_registry.node_functions()" in source
    assert "Send objects" in source


def test_supervisor_parallel_selection_is_bounded() -> None:
    policy = InvestigationGovernancePolicy(
        AutonomousBudget(
            max_iterations=4,
            max_tasks=8,
            max_parallel_tasks=2,
            max_queries=4,
            max_llm_tokens=100000,
            max_active_execution_seconds=600,
        )
    )
    plan = InvestigationPlan(
        objective="o",
        strategy="s",
        tasks=[
            InvestigationTask(task_id=f"t{i}", title=f"T{i}", objective="e", specialist="acquiring")
            for i in range(3)
        ],
    )
    with pytest.raises(InvestigationGovernanceError):
        policy.validate_supervisor_decision(
            SupervisorDecision(
                action=SupervisorAction.DELEGATE,
                next_task_ids=["t0", "t1", "t2"],
            ),
            plan=plan,
            usage=AutonomousBudgetUsage(),
            enabled_roles={"acquiring"},
        )


def test_query_slot_reservation_is_idempotent_across_sql_repairs() -> None:
    policy = TaskBudgetPolicy(
        TaskBudget(max_attempts=3, max_replans=1, max_llm_tokens=1000, max_queries=1)
    )
    first = policy.evaluate_query_proposal(TaskBudgetUsage())
    second = policy.evaluate_query_proposal(first.usage)
    third = policy.evaluate_query_proposal(second.usage)
    migrated = policy.evaluate_query_proposal(
        TaskBudgetUsage(queries=3, exhausted_reasons=["max_queries"])
    )

    assert first.approved is True
    assert second.approved is True
    assert third.approved is True
    assert first.usage.queries == 1
    assert second.usage.queries == 1
    assert third.usage.queries == 1
    assert migrated.approved is True
    assert migrated.usage.queries == 1
    assert "max_queries" not in migrated.usage.exhausted_reasons


def test_query_cost_reservation_replaces_previous_sql_candidate() -> None:
    policy = TaskBudgetPolicy(
        TaskBudget(
            max_attempts=3,
            max_queries=1,
            max_plan_cost_total=100,
            max_plan_rows_total=1_000,
            max_relation_bytes_total=10_000,
        )
    )
    stale = TaskBudgetUsage(
        queries=1,
        plan_cost_total=95,
        plan_rows_total=950,
        relation_bytes_total=9_500,
        exhausted_reasons=["max_plan_cost_total"],
    )
    repaired = policy.evaluate_query_proposal(
        stale,
        cost=CostValidation(
            approved=True,
            total_cost=20,
            plan_rows=200,
            max_node_rows=200,
            relation_bytes=2_000,
        ),
    )

    assert repaired.approved is True
    assert repaired.usage.queries == 1
    assert repaired.usage.plan_cost_total == 20
    assert repaired.usage.plan_rows_total == 200
    assert repaired.usage.relation_bytes_total == 2_000
    assert "max_plan_cost_total" not in repaired.usage.exhausted_reasons
