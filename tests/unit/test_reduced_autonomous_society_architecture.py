from __future__ import annotations

from pathlib import Path

import yaml

from axiz.pe.sql_agent.models.society import SocietyRole, society_role_contracts


ROOT = Path(__file__).resolve().parents[2]


def test_only_four_reasoning_roles_are_configured() -> None:
    payload = yaml.safe_load((ROOT / "config/agents.yaml").read_text(encoding="utf-8"))
    assert set(payload["agents"]) == {
        "investigation_coordinator",
        "domain_analyst",
        "sql_engineer",
        "evidence_reviewer",
    }


def test_specialists_are_capability_profiles_not_model_agents() -> None:
    payload = yaml.safe_load((ROOT / "config/specialists.yaml").read_text(encoding="utf-8"))
    executable = [
        value for value in payload["specialists"].values() if not value.get("critical_reviewer")
    ]
    assert executable
    assert {value["model_agent_name"] for value in executable} == {"domain_analyst"}
    critic = next(
        value for value in payload["specialists"].values() if value.get("critical_reviewer")
    )
    assert critic["model_agent_name"] == "evidence_reviewer"


def test_role_contracts_expose_json_schema_and_prohibit_direct_execution() -> None:
    contracts = society_role_contracts()
    assert {item.role for item in contracts} == set(SocietyRole)
    for contract in contracts:
        assert contract.input_contract.get("properties")
        assert contract.output_contract.get("properties")
        assert contract.may_execute_sql is False
        assert "bypass HITL" in contract.prohibited_actions


def test_sql_parsing_uses_ast_as_primary_mechanism() -> None:
    source = (ROOT / "src/axiz/pe/sql_agent/tools/temporal_query_shape.py").read_text(
        encoding="utf-8"
    )
    assert "SqlAstAnalyzer" in source
    assert "analyzer.parse(sql)" in source
    feedback = (
        ROOT / "src/axiz/pe/sql_agent/skills/sql/feedback_planning.py"
    ).read_text(encoding="utf-8")
    assert "_MONTH_DELTA_PATTERNS" not in feedback
    assert "_COMPARISON_ABSOLUTE_PATTERNS" not in feedback
    assert "import re" not in feedback
    assert "SQL-native revision envelope" in feedback
