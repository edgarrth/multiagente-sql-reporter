from pathlib import Path

from axiz.pe.sql_agent.services.agent_skills import AgentSkillRegistry


ROOT = Path(__file__).resolve().parents[2]


def test_registry_publishes_exactly_four_agent_skills() -> None:
    registry = AgentSkillRegistry(ROOT / "config/agent_skills.yaml")
    assert set(registry.contracts()) == {
        "investigation_coordinator",
        "domain_analyst",
        "sql_engineer",
        "evidence_reviewer",
    }


def test_sql_engineer_skill_contains_context_contract_and_limitations() -> None:
    registry = AgentSkillRegistry(ROOT / "config/agent_skills.yaml")
    skill = registry.get("sql_engineer")
    prefix = skill.system_prefix("interpret_feedback")
    assert "PERSONALITY:" in prefix
    assert "OPERATING CONTEXT:" in prefix
    assert "INPUT CONTRACT: FeedbackInterpretationInvocation" in prefix
    assert "OUTPUT CONTRACT: SqlFeedbackPlan" in prefix
    assert "Never use regex or phrase dictionaries" in prefix


def test_agent_directory_has_no_legacy_subpackages_or_agent_classes() -> None:
    root = ROOT / "src/axiz/pe/sql_agent/agents"
    assert not (root / "autonomous").exists()
    assert not (root / "society").exists()
    files = {path.name for path in root.glob("*_agent.py")}
    assert files == {
        "investigation_coordinator_agent.py",
        "domain_analyst_agent.py",
        "sql_engineer_agent.py",
        "evidence_reviewer_agent.py",
    }
