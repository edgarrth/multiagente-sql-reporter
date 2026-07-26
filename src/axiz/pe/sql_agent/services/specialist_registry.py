from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from axiz.pe.sql_agent.models.contracts import TaskBudget
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool

_SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class SpecialistProfile(BaseModel):
    """Configuration-only contract for one specialist subgraph.

    A new specialist is added by publishing a profile in ``config/specialists.yaml`` and
    its semantic contracts. The parent graph does not contain a hard-coded role switch.
    """

    role: str
    display_name: str
    model_agent_name: str
    description: str
    domains: list[str] = Field(default_factory=list)
    required_catalog_terms: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    instructions: str = ""
    enabled: bool = True
    critical_reviewer: bool = False
    task_budget: TaskBudget = Field(default_factory=TaskBudget)
    cache_ttl_seconds: int = Field(default=900, ge=0)
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def validate_role(self) -> "SpecialistProfile":
        if not _SAFE_ID.match(self.role):
            raise ValueError(
                "specialist role must match ^[a-z][a-z0-9_-]{1,63}$"
            )
        if not self.domains:
            raise ValueError("specialist profile must declare at least one domain or '*'")
        return self

    @property
    def graph_node_name(self) -> str:
        return f"specialist__{self.role.replace('-', '_')}"


class SpecialistRegistry:
    """Dynamic specialist registry derived from config and published semantic domains."""

    def __init__(self, path: Path, catalog: SemanticCatalogTool) -> None:
        self.path = path
        self.catalog = catalog
        self._profiles = self._load()

    def _catalog_snapshot(self) -> tuple[set[str], str, str]:
        domains = {item["name"] for item in self.catalog.list_domains()}
        symbols = {
            domain: self.catalog.semantic_symbols(domain)
            for domain in sorted(domains)
        }
        serialized = json.dumps(symbols, ensure_ascii=False, sort_keys=True, default=str)
        fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return domains, serialized.lower(), fingerprint

    def _load(self) -> dict[str, SpecialistProfile]:
        if not self.path.exists():
            raise ValueError(f"Specialist registry not found: {self.path}")
        payload = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        raw_profiles = payload.get("specialists") or {}
        if not isinstance(raw_profiles, dict) or not raw_profiles:
            raise ValueError("Specialist registry must contain a non-empty specialists mapping")

        domains, searchable_catalog, _ = self._catalog_snapshot()
        profiles: dict[str, SpecialistProfile] = {}
        for raw_role, raw_value in raw_profiles.items():
            role = str(raw_role).strip()
            raw = dict(raw_value or {})
            configured_domains = [str(item) for item in raw.get("domains", [])]
            domain_available = "*" in configured_domains or bool(
                domains.intersection(configured_domains)
            )
            terms = [str(item).lower() for item in raw.get("required_catalog_terms", [])]
            terms_available = all(term in searchable_catalog for term in terms)
            critical_reviewer = bool(raw.get("critical_reviewer", role == "critic"))
            enabled = critical_reviewer or (domain_available and terms_available)
            reasons: list[str] = []
            if not domain_available and not critical_reviewer:
                reasons.append("ningún dominio requerido está publicado")
            if not terms_available and not critical_reviewer:
                reasons.append("faltan contratos semánticos requeridos")
            profile = SpecialistProfile(
                role=role,
                display_name=str(raw.get("display_name") or role),
                model_agent_name=str(raw.get("model_agent_name") or f"{role}_specialist"),
                description=str(raw.get("description") or ""),
                domains=configured_domains or ["*"],
                required_catalog_terms=terms,
                capabilities=[str(item) for item in raw.get("capabilities", [])],
                instructions=str(raw.get("instructions") or ""),
                enabled=bool(raw.get("enabled", True)) and enabled,
                critical_reviewer=critical_reviewer,
                task_budget=TaskBudget.model_validate(raw.get("task_budget") or {}),
                cache_ttl_seconds=int(raw.get("cache_ttl_seconds", 900)),
                unavailable_reason="; ".join(reasons) if reasons else None,
            )
            if role in profiles:
                raise ValueError(f"Duplicate specialist role: {role}")
            profiles[role] = profile

        if not any(profile.critical_reviewer for profile in profiles.values()):
            raise ValueError("Specialist registry must include one critical reviewer")
        if not any(profile.enabled and not profile.critical_reviewer for profile in profiles.values()):
            raise ValueError("No executable specialist is enabled")
        return profiles

    def reload(self) -> None:
        self._profiles = self._load()

    def profile(self, role: str | Any) -> SpecialistProfile:
        key = str(getattr(role, "value", role))
        if key not in self._profiles:
            raise KeyError(f"Unknown specialist: {key}")
        return self._profiles[key]

    def executable_profiles(self) -> list[SpecialistProfile]:
        return [
            item
            for item in self._profiles.values()
            if item.enabled and not item.critical_reviewer
        ]

    def critic_profile(self) -> SpecialistProfile:
        return next(item for item in self._profiles.values() if item.critical_reviewer)

    def available_for_planning(self) -> list[dict[str, Any]]:
        return [
            profile.model_dump(mode="json")
            for profile in sorted(self._profiles.values(), key=lambda item: item.role)
        ]

    def enabled_roles(self) -> set[str]:
        return {profile.role for profile in self.executable_profiles()}

    def graph_node_names(self) -> dict[str, str]:
        return {profile.role: profile.graph_node_name for profile in self.executable_profiles()}

    def catalog_fingerprint(self) -> str:
        return self._catalog_snapshot()[2]
