from __future__ import annotations

import json
from pathlib import Path

import pytest

from axiz.pe.sql_agent.agents.autonomous.complexity_router_agent import (
    AutonomousComplexityRouterAgent,
)
from axiz.pe.sql_agent.agents.autonomous.domain_specialist_agent import (
    DomainSpecialistAgent,
)
from axiz.pe.sql_agent.models.contracts import (
    AutonomousBudget,
    AutonomousRoutingDecision,
    ConversationMemory,
    InvestigationMode,
    InvestigationTask,
    SpecialistTaskOutput,
)
from axiz.pe.sql_agent.services.agent_cache import AgentResponseCache, InMemoryJsonCache
from axiz.pe.sql_agent.tools.example_selector import ExampleSelectorTool
from axiz.pe.sql_agent.tools.proposal_review_policy import ProposalReviewPolicy
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool
from axiz.pe.sql_agent.tools.semantic_context_projection import SemanticContextProjector

ROOT = Path(__file__).resolve().parents[2]


def test_compact_projection_preserves_governance_and_reduces_context() -> None:
    catalog = SemanticCatalogTool(ROOT / "semantic_catalog")
    domain = catalog.list_domains()[0]["name"]
    full = {
        "domain_definition": catalog.get_domain(domain)["domain"],
        "allowed_sources": catalog.allowed_sources(domain),
        "query_policy": catalog.policies(domain),
        "semantic_symbols": catalog.semantic_symbols(domain),
        "catalog_hits": catalog.search("indicadores agregados periodo", domain=domain, limit=12),
        "selected_examples": ExampleSelectorTool(catalog).select(
            "indicadores agregados periodo", domain, limit=4
        ),
    }
    projector = SemanticContextProjector(
        max_catalog_documents=3,
        max_examples=1,
        max_metrics=6,
        max_dimensions=8,
    )
    compact = projector.project(
        question="consulta analítica agregada para un periodo",
        full_context=full,
        catalog_focus=["métricas certificadas", "periodo"],
    )
    assert compact["allowed_sources"] == full["allowed_sources"]
    assert compact["query_policy"] == full["query_policy"]
    assert len(compact["catalog_hits"]) <= 3
    assert len(compact["selected_examples"]) <= 1
    assert compact["projection_metadata"]["fingerprint"]
    assert len(json.dumps(compact, default=str)) < len(json.dumps(full, default=str))


def test_review_policy_skips_simple_proposal_but_escalates_risk() -> None:
    policy = ProposalReviewPolicy("postgres", high_cost_ratio=0.7, high_row_ratio=0.7)
    simple = policy.evaluate(
        task={"expected_evidence": ["one result"]},
        generated_contract={"source_objects": ["semantic.one"], "assumptions": []},
        final_sql=(
            "SELECT metric FROM semantic.one "
            "WHERE day >= CURRENT_DATE - INTERVAL '7 days' LIMIT 100"
        ),
        semantic_context={"projection_metadata": {"fingerprint": "abc"}},
        security_validation={"approved": True},
        cost_validation={
            "approved": True,
            "total_cost": 10,
            "max_plan_cost": 1000,
            "max_node_rows": 100,
            "max_plan_rows": 10000,
        },
    )
    assert simple.requires_llm_review is False

    risky = policy.evaluate(
        task={"expected_evidence": ["one", "two"]},
        generated_contract={
            "source_objects": ["semantic.one", "semantic.two"],
            "assumptions": ["semantic assumption"],
        },
        final_sql="SELECT * FROM semantic.one JOIN semantic.two ON one.id = two.id",
        semantic_context={"projection_metadata": {"fingerprint": "abc"}},
        security_validation={"approved": True},
        cost_validation={
            "approved": True,
            "total_cost": 800,
            "max_plan_cost": 1000,
            "max_node_rows": 9000,
            "max_plan_rows": 10000,
        },
    )
    assert risky.requires_llm_review is True
    assert {
        "multiple_sources",
        "semantic_assumptions",
        "high_plan_cost",
        "high_plan_rows",
    }.issubset(set(risky.reasons))


def test_multiple_expected_indicators_do_not_force_llm_review() -> None:
    policy = ProposalReviewPolicy("postgres")
    decision = policy.evaluate(
        task={"expected_evidence": ["monto", "cantidad", "tasa de aprobación"]},
        generated_contract={
            "source_objects": ["semantic.acquiring_monthly_kpis"],
            "assumptions": [],
            "selected_metrics": ["total_amount", "transaction_count", "approval_rate"],
            "selected_dimensions": ["month"],
        },
        final_sql=(
            "SELECT month, total_amount, transaction_count, approval_rate "
            "FROM semantic.acquiring_monthly_kpis "
            "WHERE month = DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month') "
            "LIMIT 100"
        ),
        semantic_context={
            "allowed_sources": ["semantic.acquiring_monthly_kpis"],
            "semantic_symbols": {
                "metrics": [
                    {"name": "total_amount"},
                    {"name": "transaction_count"},
                    {"name": "approval_rate"},
                ],
                "dimensions": [{"name": "month"}],
            },
            "projection_metadata": {"fingerprint": "catalog-v4"},
        },
        security_validation={"approved": True},
        cost_validation={
            "approved": True,
            "total_cost": 20,
            "max_plan_cost": 1000,
            "max_node_rows": 12,
            "max_plan_rows": 10000,
        },
    )
    assert decision.requires_llm_review is False
    assert "multiple_expected_evidence" not in decision.reasons


def test_specialist_review_payload_excludes_explain_tree_and_is_bounded() -> None:
    task = InvestigationTask(
        task_id="direct-kpi",
        title="Indicadores del último mes",
        objective="Obtener indicadores certificados del último mes",
        specialist="acquiring",
        domain="acquiring",
        expected_evidence=["monto", "cantidad", "tasa de aprobación"],
    )
    prepared = SpecialistTaskOutput(
        task_id=task.task_id,
        specialist="acquiring",
        refined_question="Muestra los indicadores certificados del último mes cerrado",
        domain="acquiring",
        expected_evidence=task.expected_evidence,
        catalog_focus=["indicadores", "último mes"],
    )
    huge_explain = {
        "Plan": {
            "Node Type": "Hash Join",
            "Plans": [
                {
                    "Node Type": "Seq Scan",
                    "Relation Name": f"fact_{index}",
                    "Filter": "merchant_filter_" + ("x" * 2500),
                }
                for index in range(80)
            ],
        }
    }
    generated = {
        "interpretation": "Indicadores mensuales",
        "assumptions": [],
        "selected_metrics": ["total_amount", "transaction_count"],
        "selected_dimensions": ["month"],
        "source_objects": ["semantic.acquiring_monthly_kpis"],
    }
    security = {
        "approved": True,
        "statement_type": "SELECT",
        "tables": ["semantic.acquiring_monthly_kpis"],
        "columns": ["month", "total_amount", "transaction_count"],
        "violations": [],
    }
    cost = {
        "approved": True,
        "engine": "postgres",
        "total_cost": 125.4,
        "plan_rows": 12,
        "max_node_rows": 12,
        "plan_node_count": 81,
        "warnings": [],
        "explain_plan": huge_explain,
    }
    semantic = {
        "allowed_sources": ["semantic.acquiring_monthly_kpis"],
        "semantic_symbols": {"metrics": [{"name": "total_amount"}]},
        "projection_metadata": {"fingerprint": "catalog-v4"},
    }
    sql = "SELECT month, total_amount, transaction_count FROM semantic.acquiring_monthly_kpis"

    compact = DomainSpecialistAgent.build_review_payload(
        task=task,
        prepared=prepared,
        generated_contract=generated,
        final_sql=sql,
        semantic_context=semantic,
        security_validation=security,
        cost_validation=cost,
    )
    old_style = {
        "task": task.model_dump(mode="json"),
        "prepared_task": prepared.model_dump(mode="json"),
        "generated_contract": generated,
        "final_sql": sql,
        "semantic_context": semantic,
        "security_validation": security,
        "cost_validation": cost,
    }

    assert "explain_plan" not in compact["cost_validation"]
    assert compact["cost_validation"]["plan_node_count"] == 81
    assert len(json.dumps(compact, ensure_ascii=False)) < (
        len(json.dumps(old_style, ensure_ascii=False)) * 0.1
    )


def test_direct_routing_contract_requires_one_specialist() -> None:
    decision = AutonomousRoutingDecision(
        mode=InvestigationMode.DIRECT_SPECIALIST,
        specialist="acquiring",
        task_objective="Obtener una evidencia gobernada",
    )
    assert decision.mode == InvestigationMode.DIRECT_SPECIALIST


def test_graph_contains_general_adaptive_route_and_direct_grounded_completion() -> None:
    graph = (ROOT / "src/axiz/pe/sql_agent/workflow/graph.py").read_text(encoding="utf-8")
    nodes = (ROOT / "src/axiz/pe/sql_agent/workflow/nodes.py").read_text(encoding="utf-8")
    specialist = (
        ROOT / "src/axiz/pe/sql_agent/workflow/subgraphs/specialist.py"
    ).read_text(encoding="utf-8")
    assert '"select_investigation_mode"' in graph
    assert '"synthesize_direct_investigation"' in graph
    assert "DIRECT_SPECIALIST" in nodes
    assert "FULL_INVESTIGATION" in nodes
    assert "compact=True" in specialist
    assert "review_policy.evaluate" in specialist
    assert "hydrate_prepared_task" in specialist


def test_cache_namespace_is_versioned_for_new_context_contracts() -> None:
    config = (ROOT / "src/axiz/pe/sql_agent/config.py").read_text(encoding="utf-8")
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    cache = AgentResponseCache(InMemoryJsonCache())
    projector = SemanticContextProjector()

    assert "axiz:agent-cache:v6" in config
    assert "AGENT_CACHE_NAMESPACE=axiz:agent-cache:v6" in env
    assert cache.namespace == "axiz:agent-cache:v6"
    assert projector.configuration() == {
        "contract_version": "semantic-context-v6",
        "max_catalog_documents": 4,
        "max_examples": 1,
        "max_metrics": 10,
        "max_dimensions": 12,
        "max_document_items": 8,
    }


@pytest.mark.asyncio
async def test_adaptive_router_cache_avoids_repeated_model_call() -> None:
    class FakeLLM:
        agent_name = "autonomous_router"

        def __init__(self) -> None:
            self.calls = 0

        async def parse(self, *, system, user, response_model):
            self.calls += 1
            return response_model(
                mode="direct_specialist",
                specialist="acquiring",
                domain="acquiring",
                task_objective="Obtener una evidencia",
            )

    llm = FakeLLM()
    router = AutonomousComplexityRouterAgent(
        llm, AgentResponseCache(InMemoryJsonCache(), namespace="router-test")
    )
    kwargs = {
        "question": "Solicitud analítica autocontenida",
        "relation": "independent_request",
        "domain": "acquiring",
        "memory": ConversationMemory(),
        "specialists": [
            {
                "role": "acquiring",
                "display_name": "Acquiring",
                "description": "Analítica",
                "domains": ["acquiring"],
                "capabilities": ["analytics"],
                "enabled": True,
                "critical_reviewer": False,
            }
        ],
        "published_domains": [{"name": "acquiring"}],
        "budget": AutonomousBudget(
            max_iterations=4,
            max_tasks=8,
            max_queries=4,
            max_llm_tokens=100000,
            max_active_execution_seconds=600,
        ),
        "catalog_fingerprint": "catalog-v1",
    }
    first = await router.route(**kwargs)
    second = await router.route(**kwargs)
    assert first == second
    assert llm.calls == 1


def test_adaptive_core_has_no_specialist_or_domain_specific_branches() -> None:
    import yaml

    profiles = yaml.safe_load((ROOT / "config/specialists.yaml").read_text(encoding="utf-8"))
    roles = {str(role) for role in (profiles.get("specialists") or {}).keys()}
    sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8").lower()
        for path in (
            "src/axiz/pe/sql_agent/agents/autonomous/complexity_router_agent.py",
            "src/axiz/pe/sql_agent/tools/semantic_context_projection.py",
            "src/axiz/pe/sql_agent/tools/proposal_review_policy.py",
        )
    )
    for role in roles:
        assert f'== "{role}"' not in sources
        assert f"== '{role}'" not in sources


def test_streamlit_branding_uses_packaged_icon_for_favicon_and_interface() -> None:
    app = (ROOT / "streamlit_app/app.py").read_text(encoding="utf-8")
    icon = ROOT / "streamlit_app/assets/axiz-agent-icon.png"
    favicon = ROOT / "streamlit_app/assets/favicon.png"
    favicon_ico = ROOT / "streamlit_app/assets/favicon.ico"
    logo = ROOT / "streamlit_app/assets/axiz-logo.png"
    logo_2x = ROOT / "streamlit_app/assets/axiz-logo@2x.png"
    svg = ROOT / "streamlit_app/assets/axiz-agent-icon.svg"

    for asset in (icon, favicon, favicon_ico, logo, logo_2x, svg):
        assert asset.is_file() and asset.stat().st_size > 0
    assert "page_icon=FAVICON" in app
    assert "AXIZ_LOGO_PATH" in app
    assert "st.image(AXIZ_LOGO" in app
    theme = (ROOT / ".streamlit/config.toml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "infrastructure/streamlit.Dockerfile").read_text(encoding="utf-8")

    assert "--axiz-burgundy: #8f1d2c" in app
    assert "render_brand_header(compact=True)" in app
    assert "render_brand_header()" in app
    assert 'primaryColor = "#8F1D2C"' in theme
    assert 'backgroundColor = "#F5F7F9"' in theme
    assert "COPY .streamlit ./.streamlit" in dockerfile
