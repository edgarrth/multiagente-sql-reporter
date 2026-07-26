from __future__ import annotations

import json
from pathlib import Path

import pytest

from axiz.pe.sql_agent.agents.autonomous.complexity_router_agent import (
    AutonomousComplexityRouterAgent,
)
from axiz.pe.sql_agent.models.contracts import (
    AutonomousBudget,
    AutonomousRoutingDecision,
    ConversationMemory,
    InvestigationMode,
)
from axiz.pe.sql_agent.tools.proposal_review_policy import ProposalReviewPolicy
from axiz.pe.sql_agent.tools.example_selector import ExampleSelectorTool
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool
from axiz.pe.sql_agent.tools.semantic_context_projection import SemanticContextProjector
from axiz.pe.sql_agent.services.agent_cache import AgentResponseCache, InMemoryJsonCache

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
        final_sql="SELECT metric FROM semantic.one WHERE day >= CURRENT_DATE - INTERVAL '7 days' LIMIT 100",
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
    assert {"multiple_sources", "semantic_assumptions", "high_plan_cost", "high_plan_rows"}.issubset(set(risky.reasons))


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
    specialist = (ROOT / "src/axiz/pe/sql_agent/workflow/subgraphs/specialist.py").read_text(encoding="utf-8")
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
    assert "axiz:agent-cache:v2" in config
    assert "AGENT_CACHE_NAMESPACE=axiz:agent-cache:v2" in env


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
    svg = ROOT / "streamlit_app/assets/axiz-agent-icon.svg"

    assert icon.is_file() and icon.stat().st_size > 0
    assert favicon.is_file() and favicon.stat().st_size > 0
    assert svg.is_file() and svg.stat().st_size > 0
    assert "page_icon=FAVICON" in app
    assert "render_brand_header(compact=True)" in app
    assert "render_brand_header()" in app
