from pathlib import Path

import pytest

from axiz.pe.sql_agent.models.contracts import (
    AutonomousBudget,
    AutonomousBudgetUsage,
    InvestigationPlan,
    InvestigationQueryMode,
    InvestigationTask,
    SpecialistRole,
    SupervisorAction,
    SupervisorDecision,
)
from axiz.pe.sql_agent.services.llm_usage import LLMRunBudgetExceeded, LLMUsageCollector
from axiz.pe.sql_agent.services.specialist_registry import SpecialistRegistry
from axiz.pe.sql_agent.tools.investigation_governance import (
    InvestigationGovernanceError,
    InvestigationGovernancePolicy,
)
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool

ROOT = Path(__file__).resolve().parents[2]


def budget() -> AutonomousBudget:
    return AutonomousBudget(
        max_iterations=4,
        max_tasks=8,
        max_queries=4,
        max_llm_tokens=120_000,
        max_active_execution_seconds=600,
    )


def registry() -> SpecialistRegistry:
    return SpecialistRegistry(
        ROOT / "config" / "specialists.yaml",
        SemanticCatalogTool(ROOT / "semantic_catalog"),
    )


def test_specialist_availability_is_derived_from_published_catalog() -> None:
    profiles = {item["role"]: item for item in registry().available_for_planning()}
    assert profiles["acquiring"]["enabled"] is True
    assert profiles["chargebacks"]["enabled"] is True
    assert profiles["temporal"]["enabled"] is True
    assert profiles["issuing"]["enabled"] is False
    assert profiles["fraud"]["enabled"] is False
    assert profiles["critic"]["enabled"] is True


def test_governance_accepts_minimal_multispecialist_plan() -> None:
    policy = InvestigationGovernancePolicy(budget())
    plan = InvestigationPlan(
        objective="Explicar una variación de aprobación",
        strategy="Comparar nivel y tendencia",
        tasks=[
            InvestigationTask(
                task_id="acq",
                title="Nivel por canal",
                objective="Medir aprobación por canal",
                specialist=SpecialistRole.ACQUIRING,
            ),
            InvestigationTask(
                task_id="trend",
                title="Tendencia",
                objective="Comparar periodos equivalentes",
                specialist=SpecialistRole.TEMPORAL,
                dependencies=["acq"],
            ),
        ],
    )
    governed = policy.validate_plan(plan, enabled_roles=registry().enabled_roles())
    assert [task.task_id for task in governed.plan.tasks] == ["acq", "trend"]


def test_governance_rejects_unpublished_specialist_and_dependency_cycles() -> None:
    policy = InvestigationGovernancePolicy(budget())
    unavailable = InvestigationPlan(
        objective="Investigar emisión",
        strategy="Usar el dominio publicado",
        tasks=[
            InvestigationTask(
                task_id="iss",
                title="Emisión",
                objective="Analizar emisión",
                specialist=SpecialistRole.ISSUING,
            )
        ],
    )
    with pytest.raises(InvestigationGovernanceError):
        policy.validate_plan(unavailable, enabled_roles=registry().enabled_roles())

    cyclic = InvestigationPlan(
        objective="Ciclo inválido",
        strategy="No aplicable",
        tasks=[
            InvestigationTask(
                task_id="a",
                title="A",
                objective="A",
                specialist=SpecialistRole.ACQUIRING,
                dependencies=["b"],
            ),
            InvestigationTask(
                task_id="b",
                title="B",
                objective="B",
                specialist=SpecialistRole.TEMPORAL,
                dependencies=["a"],
            ),
        ],
    )
    with pytest.raises(InvestigationGovernanceError):
        policy.validate_plan(cyclic, enabled_roles=registry().enabled_roles())


def test_previous_sql_revision_is_allowed_only_for_a_real_follow_up() -> None:
    policy = InvestigationGovernancePolicy(budget())
    plan = InvestigationPlan(
        objective="Modificar análisis previo",
        strategy="Preservar invariantes",
        tasks=[
            InvestigationTask(
                task_id="revision",
                title="Revisión",
                objective="Aplicar el cambio solicitado",
                specialist=SpecialistRole.ACQUIRING,
                query_mode=InvestigationQueryMode.REVISE_PREVIOUS,
            )
        ],
    )
    with pytest.raises(InvestigationGovernanceError):
        policy.validate_plan(
            plan,
            enabled_roles=registry().enabled_roles(),
            allow_previous_sql_revision=False,
        )
    accepted = policy.validate_plan(
        plan,
        enabled_roles=registry().enabled_roles(),
        allow_previous_sql_revision=True,
    )
    assert accepted.plan.tasks[0].query_mode == InvestigationQueryMode.REVISE_PREVIOUS


def test_supervisor_cannot_finalize_without_evidence_but_can_reject_a_conclusion() -> None:
    policy = InvestigationGovernancePolicy(budget())
    plan = InvestigationPlan(
        objective="Investigar",
        strategy="Una tarea",
        tasks=[
            InvestigationTask(
                task_id="task-1",
                title="Tarea",
                objective="Obtener evidencia",
                specialist=SpecialistRole.ACQUIRING,
            )
        ],
    )
    with pytest.raises(InvestigationGovernanceError):
        policy.validate_supervisor_decision(
            SupervisorDecision(action=SupervisorAction.FINALIZE),
            plan=plan,
            usage=AutonomousBudgetUsage(),
            enabled_roles=registry().enabled_roles(),
        )

    decision = policy.validate_supervisor_decision(
        SupervisorDecision(
            action=SupervisorAction.REJECT_CONCLUSION,
            rejected_conclusions=["No existe evidencia para atribuir causalidad"],
        ),
        plan=plan,
        usage=AutonomousBudgetUsage(queries_executed=1),
        enabled_roles=registry().enabled_roles(),
    )
    assert decision.action == SupervisorAction.REJECT_CONCLUSION
    assert decision.next_task_id is None


def test_query_and_llm_budgets_fail_closed() -> None:
    policy = InvestigationGovernancePolicy(budget())
    policy.assert_query_budget(3)
    with pytest.raises(InvestigationGovernanceError):
        policy.assert_query_budget(4)

    collector = LLMUsageCollector(max_total_tokens=100)
    collector.assert_can_reserve(100, agent="planner")
    with pytest.raises(LLMRunBudgetExceeded):
        collector.assert_can_reserve(101, agent="planner")


def test_graph_keeps_security_cost_and_hitl_between_specialists_and_execution() -> None:
    graph = (ROOT / "src/axiz/pe/sql_agent/workflow/graph.py").read_text(encoding="utf-8")
    assert 'graph.add_edge("initialize_society", "select_investigation_mode")' in graph
    assert '"plan_investigation": "plan_investigation"' in graph
    assert 'graph.add_edge("plan_investigation", "supervisor_review")' in graph
    assert 'graph.add_edge("estimate_llm_approval", "human_review")' in graph
    assert '"execute_sql": "execute_sql"' in graph
    assert 'route_after_evidence_recorded' in graph
    assert 'graph.add_conditional_edges(' in graph
    assert '"record_evidence"' in graph
    assert 'graph.add_edge("critic_review", "supervisor_review")' in graph


def test_autonomous_models_ui_and_specialist_endpoint_are_wired() -> None:
    models = (ROOT / "config/agents.yaml").read_text(encoding="utf-8")
    for name in (
        "investigation_coordinator:",
        "domain_analyst:",
        "sql_engineer:",
        "evidence_reviewer:",
    ):
        assert name in models
    assert "autonomous_router:" not in models
    assert "acquiring_specialist:" not in models

    ui = (ROOT / "streamlit_app/app.py").read_text(encoding="utf-8")
    routes = (ROOT / "src/axiz/pe/sql_agent/api/routes/catalog.py").read_text(
        encoding="utf-8"
    )
    assert "Investigación autónoma" in ui
    assert '@router.get("/specialists")' in routes
