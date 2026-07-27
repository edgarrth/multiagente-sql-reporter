from axiz.pe.sql_agent.models.contracts import (
    AutonomousBudget,
    AutonomousBudgetUsage,
    CostValidation,
    SpecialistProposalReview,
    SpecialistProposalStatus,
)
from axiz.pe.sql_agent.tools.investigation_governance import InvestigationGovernancePolicy
from axiz.pe.sql_agent.tools.proposal_governance import SpecialistProposalGovernance


def test_cached_proposal_must_pass_current_security_cost_and_review() -> None:
    approved = SpecialistProposalGovernance.evaluate(
        error=None,
        cache_hit=True,
        security_validation={"approved": True},
        cost_validation={"approved": True},
        review=SpecialistProposalReview(approved=True),
        task_budget_approved=True,
    )
    assert approved.status == SpecialistProposalStatus.CACHE_HIT

    rejected = SpecialistProposalGovernance.evaluate(
        error=None,
        cache_hit=True,
        security_validation={"approved": False},
        cost_validation={"approved": True},
        review=SpecialistProposalReview(approved=True),
        task_budget_approved=True,
    )
    assert rejected.status == SpecialistProposalStatus.BLOCKED
    assert "security" in (rejected.block_reason or "").lower()


def test_specialist_self_rejection_cannot_reach_hitl() -> None:
    decision = SpecialistProposalGovernance.evaluate(
        error=None,
        cache_hit=False,
        security_validation={"approved": True},
        cost_validation={"approved": True},
        review=SpecialistProposalReview(
            approved=False,
            retry_instruction="Correct the metric grain",
        ),
        task_budget_approved=True,
    )
    assert decision.status == SpecialistProposalStatus.FAILED
    assert decision.block_reason == "Correct the metric grain"


def test_cumulative_query_budget_is_evaluated_before_hitl() -> None:
    policy = InvestigationGovernancePolicy(
        AutonomousBudget(
            max_iterations=4,
            max_tasks=8,
            max_parallel_tasks=2,
            max_queries=2,
            max_llm_tokens=100000,
            max_active_execution_seconds=600,
            max_total_plan_cost=100,
            max_total_plan_rows=1000,
            max_total_relation_bytes=10000,
            max_total_database_seconds=30,
        )
    )
    usage = AutonomousBudgetUsage(
        queries_executed=1,
        total_plan_cost=80,
        total_plan_rows=900,
        total_relation_bytes=9000,
    )
    violations = policy.proposal_budget_violations(
        usage,
        CostValidation(
            approved=True,
            total_cost=30,
            plan_rows=200,
            max_node_rows=200,
            relation_bytes=2000,
        ),
    )
    assert set(violations) == {
        "max_total_plan_cost",
        "max_total_plan_rows",
        "max_total_relation_bytes",
    }


def test_regeneration_removes_cache_provenance_in_specialist_subgraph() -> None:
    source = open(
        "src/axiz/pe/sql_agent/workflow/subgraphs/specialist.py",
        encoding="utf-8",
    ).read()
    assert '"cache_hit": False' in source
    assert "SpecialistProposalGovernance.evaluate" in source


def test_cost_gate_preserves_actionable_validation_reason() -> None:
    decision = SpecialistProposalGovernance.evaluate(
        error=None,
        cache_hit=False,
        security_validation={"approved": True},
        cost_validation={
            "approved": False,
            "warnings": ["Estimated rows exceed max_plan_rows=250000"],
            "error_message": None,
        },
        review=SpecialistProposalReview(approved=True),
        task_budget_approved=True,
    )

    assert decision.status == SpecialistProposalStatus.BLOCKED
    assert "query-cost validation" in (decision.block_reason or "")
    assert "Estimated rows exceed" in (decision.block_reason or "")
