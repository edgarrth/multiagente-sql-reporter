from pathlib import Path

import pytest

from axiz.pe.sql_agent.models.contracts import (
    ContextResolutionInvocation,
    ContextResolutionOutput,
)
from axiz.pe.sql_agent.services.agent_skills import AgentSkillRegistry

ROOT = Path(__file__).resolve().parents[2]


def test_agent_skill_registry_validates_yaml_contract_names() -> None:
    registry = AgentSkillRegistry(ROOT / "config/agent_skills.yaml")
    assert registry.get("investigation_coordinator").modes["context"].input_contract == (
        "ContextResolutionInvocation"
    )


def test_agent_skill_spec_can_validate_typed_skill_contracts() -> None:
    spec = AgentSkillRegistry(ROOT / "config/agent_skills.yaml").get(
        "investigation_coordinator"
    )
    spec.assert_mode_contracts(
        "context",
        ContextResolutionInvocation,
        ContextResolutionOutput,
    )


def test_agent_skill_registry_rejects_unknown_contract(tmp_path: Path) -> None:
    config = tmp_path / "agent_skills.yaml"
    config.write_text(
        """
agents:
  example:
    display_name: Example
    personality: Precise
    context: Test
    modes:
      run:
        input_contract: MissingInvocation
        output_contract: ContextResolutionOutput
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="MissingInvocation"):
        AgentSkillRegistry(config)
