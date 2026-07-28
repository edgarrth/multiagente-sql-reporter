from pathlib import Path

from axiz.pe.sql_agent.models.contracts import SqlGenerationOutput
from axiz.pe.sql_agent.models.sql_artifacts import CompiledSqlArtifact, SqlSnapshot


ROOT = Path(__file__).resolve().parents[2]


def test_project_has_exactly_four_reasoning_agent_classes() -> None:
    agent_dir = ROOT / "src/axiz/pe/sql_agent/agents"
    sources = "\n".join(path.read_text(encoding="utf-8") for path in agent_dir.glob("*.py"))
    classes = [line for line in sources.splitlines() if line.startswith("class ")]
    assert len(classes) == 4
    assert {line.split("(", 1)[0].rstrip(":") for line in classes} == {
        "class InvestigationCoordinatorAgent",
        "class DomainAnalystAgent",
        "class SqlEngineerAgent",
        "class EvidenceReviewerAgent",
    }


def test_llm_sql_output_has_no_fixed_business_property_schema() -> None:
    properties = SqlGenerationOutput.model_json_schema()["properties"]
    assert set(properties) == {
        "sql",
        "interpretation",
        "assumptions",
        "change_summary",
        "requires_clarification",
        "clarification_question",
    }


def test_sql_state_is_generic_ast_snapshot_not_query_form() -> None:
    snapshot_properties = SqlSnapshot.model_json_schema()["properties"]
    assert "projections" in snapshot_properties
    assert "predicates" in snapshot_properties
    for fixed in ("metrics", "dimensions", "filters", "time_window"):
        assert fixed not in snapshot_properties
    assert CompiledSqlArtifact.model_json_schema()["properties"]["sql"]


def test_old_fixed_feedback_modules_are_removed() -> None:
    root = ROOT / "src/axiz/pe/sql_agent"
    removed = (
        "tools/sql_feedback.py",
        "tools/sql_feedback_plan.py",
        "tools/temporal_query_shape.py",
        "skills/sql/feedback_planning.py",
        "skills/sql/compliance.py",
        "services/semantic_query_spec.py",
        "models/query_spec.py",
    )
    for relative in removed:
        assert not (root / relative).exists()


def test_agent_and_skill_code_contains_no_intent_regex() -> None:
    for relative in ("agents", "skills"):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "src/axiz/pe/sql_agent" / relative).rglob("*.py")
        )
        assert "re.compile(" not in source
        assert "_MONTH_INTERVAL" not in source
        assert "SqlFeedbackPlan" not in source


def test_default_semantic_projection_keeps_all_published_members_and_sources() -> None:
    from axiz.pe.sql_agent.tools.semantic_context_projection import SemanticContextProjector

    projector = SemanticContextProjector()
    context = {
        "domain_definition": {"name": "test"},
        "allowed_sources": [f"semantic.source_{index}" for index in range(8)],
        "query_policy": {},
        "source_contracts": {
            f"semantic.source_{index}": {
                "name": f"source_{index}",
                "source": f"semantic.source_{index}",
                "columns": [f"column_{index}"],
            }
            for index in range(8)
        },
        "semantic_symbols": {
            "metrics": [{"name": f"metric_{index}"} for index in range(15)],
            "dimensions": [{"name": f"dimension_{index}"} for index in range(15)],
            "sources": [
                {"name": f"source_{index}", "source": f"semantic.source_{index}"}
                for index in range(8)
            ],
        },
        "calendar_context": {},
        "catalog_hits": [],
        "selected_examples": [],
    }
    result = projector.project(question="consulta libre", full_context=context)
    assert len(result["source_contracts"]) == 8
    assert len(result["semantic_symbols"]["metrics"]) == 15
    assert len(result["semantic_symbols"]["dimensions"]) == 15


def test_sql_engineer_does_not_whitelist_catalog_property_names() -> None:
    from axiz.pe.sql_agent.skills.sql.generation import SqlGenerationSkill

    context = {
        "allowed_sources": ["semantic.any"],
        "source_contracts": {
            "semantic.any": {
                "source": "semantic.any",
                "columns": ["id"],
                "future_semantic_property": {"behavior": "published"},
            }
        },
        "semantic_symbols": {"future_symbol_group": [{"name": "new_member"}]},
        "domain_definition": {"future_domain_property": True},
    }
    projected = SqlGenerationSkill._semantic_projection(context)
    assert projected["source_contracts"]["semantic.any"][
        "future_semantic_property"
    ] == {"behavior": "published"}
    assert projected["semantic_symbols"]["future_symbol_group"]
    assert projected["domain_definition"]["future_domain_property"] is True
