from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from axiz.pe.sql_agent.models import contracts, society

_CONTRACT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


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

    @staticmethod
    def _contract_names(contract_expression: str) -> set[str]:
        return {
            name
            for name in _CONTRACT_NAME.findall(contract_expression)
            if name not in {"or", "and"}
        }

    def assert_mode_contracts(
        self,
        mode: str,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
    ) -> None:
        mode_spec = self.modes.get(mode)
        if mode_spec is None:
            raise KeyError(f"Agent skill {self.role!r} does not publish mode {mode!r}")
        if mode_spec.input_contract != input_model.__name__:
            raise ValueError(
                f"{self.role}.{mode}: input_contract={mode_spec.input_contract!r} "
                f"does not match {input_model.__name__!r}"
            )
        output_contracts = self._contract_names(mode_spec.output_contract)
        if output_model.__name__ not in output_contracts:
            raise ValueError(
                f"{self.role}.{mode}: output_contract={mode_spec.output_contract!r} "
                f"does not include {output_model.__name__!r}"
            )

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
        self._validate_published_contracts()

    @staticmethod
    def _published_contracts() -> dict[str, type[BaseModel]]:
        result: dict[str, type[BaseModel]] = {}
        for module in (contracts, society):
            for name, value in inspect.getmembers(module, inspect.isclass):
                if issubclass(value, BaseModel):
                    result[name] = value
        return result

    def _validate_published_contracts(self) -> None:
        published = self._published_contracts()
        errors: list[str] = []
        for role, spec in sorted(self._specs.items()):
            for mode, mode_spec in sorted(spec.modes.items()):
                names = {
                    *AgentSkillSpec._contract_names(mode_spec.input_contract),
                    *AgentSkillSpec._contract_names(mode_spec.output_contract),
                }
                missing = sorted(name for name in names if name not in published)
                if missing:
                    errors.append(f"{role}.{mode}: " + ", ".join(missing))
        if errors:
            raise ValueError(
                "Agent skill registry references unknown Pydantic contracts: "
                + "; ".join(errors)
            )

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
