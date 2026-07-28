from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class AgentSkillMode(BaseModel):
    input_contract: str
    output_contract: str
    instructions: str = ""


class AgentSkillSpec(BaseModel):
    role: str
    display_name: str
    personality: str
    context: str
    responsibilities: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    modes: dict[str, AgentSkillMode] = Field(default_factory=dict)

    def system_prefix(self, mode: str) -> str:
        mode_spec = self.modes.get(mode)
        if mode_spec is None:
            raise KeyError(f"Agent skill {self.role!r} does not publish mode {mode!r}")
        responsibilities = "\n".join(f"- {item}" for item in self.responsibilities)
        limitations = "\n".join(f"- {item}" for item in self.limitations)
        return (
            f"ROLE: {self.display_name}\n"
            f"PERSONALITY: {self.personality}\n"
            f"OPERATING CONTEXT: {self.context}\n"
            f"RESPONSIBILITIES:\n{responsibilities}\n"
            f"NON-NEGOTIABLE LIMITATIONS:\n{limitations}\n"
            f"MODE: {mode}\n"
            f"INPUT CONTRACT: {mode_spec.input_contract}\n"
            f"OUTPUT CONTRACT: {mode_spec.output_contract}\n"
            f"MODE INSTRUCTIONS: {mode_spec.instructions or 'Follow the typed contract exactly.'}"
        )


class AgentSkillRegistry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        agents = raw.get("agents") or {}
        self._specs: dict[str, AgentSkillSpec] = {}
        for role, payload in agents.items():
            self._specs[str(role)] = AgentSkillSpec(role=str(role), **dict(payload or {}))

    def get(self, role: str) -> AgentSkillSpec:
        try:
            return self._specs[role]
        except KeyError as exc:
            raise KeyError(f"Unknown agent skill role={role!r}") from exc

    def contracts(self) -> dict[str, Any]:
        return {
            role: spec.model_dump(mode="json")
            for role, spec in sorted(self._specs.items())
        }
