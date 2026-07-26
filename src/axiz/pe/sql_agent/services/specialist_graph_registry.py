from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from axiz.pe.sql_agent.agents.autonomous.domain_specialist_agent import DomainSpecialistAgent
from axiz.pe.sql_agent.models.contracts import (
    InvestigationTask,
    InvestigationTrajectoryEvent,
    SpecialistQueryProposal,
)
from axiz.pe.sql_agent.services.llm import AgentModelRegistry, StructuredLLMFactory
from axiz.pe.sql_agent.services.llm_usage import llm_usage_scope
from axiz.pe.sql_agent.services.specialist_registry import SpecialistRegistry
from axiz.pe.sql_agent.workflow.subgraphs.specialist import SpecialistSubgraphFactory


class SpecialistGraphRegistry:
    """Builds one isolated LangGraph subgraph per configured specialist profile."""

    def __init__(
        self,
        *,
        registry: SpecialistRegistry,
        subgraph_factory: SpecialistSubgraphFactory,
        llm_factory: StructuredLLMFactory,
        model_registry: AgentModelRegistry,
    ) -> None:
        self.registry = registry
        self.subgraph_factory = subgraph_factory
        self.llm_factory = llm_factory
        self.model_registry = model_registry
        self._graphs: dict[str, Any] = {}
        self._build()

    def _model_fingerprint(self, agent_name: str) -> str:
        profile = self.model_registry.profile_for(agent_name).model_dump(mode="json")
        serialized = json.dumps(profile, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _build(self) -> None:
        graphs: dict[str, Any] = {}
        for profile in self.registry.executable_profiles():
            agent = DomainSpecialistAgent(
                profile,
                self.llm_factory.for_agent(profile.model_agent_name),
            )
            graphs[profile.role] = self.subgraph_factory.build(profile, agent)
        self._graphs = graphs

    def reload(self) -> None:
        self.registry.reload()
        self._build()

    def roles(self) -> set[str]:
        return set(self._graphs)

    def graph(self, specialist_id: str) -> Any:
        if specialist_id not in self._graphs:
            raise KeyError(f"No specialist subgraph is available for {specialist_id}")
        return self._graphs[specialist_id]

    def node_name(self, specialist_id: str) -> str:
        return self.registry.profile(specialist_id).graph_node_name

    def node_functions(self) -> dict[str, Callable[[dict[str, Any]], Any]]:
        result: dict[str, Callable[[dict[str, Any]], Any]] = {}
        for profile in self.registry.executable_profiles():
            result[profile.graph_node_name] = self._build_node(profile.role)
        return result

    def _build_node(self, specialist_id: str):
        async def run_specialist(parent_state: dict[str, Any]) -> dict[str, Any]:
            task_payload = parent_state.get("autonomous_dispatch_task") or parent_state.get("task")
            if not task_payload:
                raise ValueError("Specialist dispatch did not include a task")
            task = InvestigationTask.model_validate(task_payload)
            profile = self.registry.profile(specialist_id)
            subgraph_input = {
                "task": task.model_dump(mode="json"),
                "original_question": parent_state.get("question") or "",
                "conversation_memory": parent_state.get("conversation_memory") or {},
                "conversation_history": parent_state.get("conversation_history") or [],
                "published_domains": parent_state.get("published_domains")
                or parent_state.get("autonomous_published_domains")
                or [],
                "prior_evidence": parent_state.get("autonomous_evidence") or [],
                "previous_sql": parent_state.get("autonomous_previous_sql")
                or (parent_state.get("conversation_memory") or {}).get("last_sql")
                or "",
                "catalog_fingerprint": self.registry.catalog_fingerprint(),
                "model_fingerprint": self._model_fingerprint(profile.model_agent_name),
            }
            with llm_usage_scope(
                task.task_id,
                specialist_id,
                max_tokens=task.task_budget.max_llm_tokens,
            ):
                output = await self.graph(specialist_id).ainvoke(subgraph_input)
            proposal = SpecialistQueryProposal.model_validate(output["proposal"])
            base_sequence = int(parent_state.get("autonomous_trajectory_sequence") or 0)
            events = [
                InvestigationTrajectoryEvent(
                    sequence=base_sequence,
                    stage="specialist_subgraph",
                    actor=specialist_id,
                    action="security_validated",
                    task_id=task.task_id,
                    specialist_id=specialist_id,
                    wave=task.wave,
                    cache_hit=proposal.cache_hit,
                    metadata={
                        "approved": bool(proposal.security_validation.get("approved")),
                    },
                ),
                InvestigationTrajectoryEvent(
                    sequence=base_sequence + 1,
                    stage="specialist_subgraph",
                    actor=specialist_id,
                    action="cost_validated",
                    task_id=task.task_id,
                    specialist_id=specialist_id,
                    wave=task.wave,
                    cache_hit=proposal.cache_hit,
                    metadata={
                        "approved": bool(proposal.cost_validation.get("approved")),
                    },
                ),
                InvestigationTrajectoryEvent(
                    sequence=base_sequence + 2,
                    stage="specialist_subgraph",
                    actor=specialist_id,
                    action="proposal_created",
                    task_id=task.task_id,
                    specialist_id=specialist_id,
                    wave=task.wave,
                    cache_hit=proposal.cache_hit,
                    metadata={
                        "proposal_id": proposal.proposal_id,
                        "status": proposal.status.value,
                    },
                ),
            ]
            return {
                "autonomous_proposal_updates": [proposal.model_dump(mode="json")],
                "autonomous_trajectory_updates": [
                    event.model_dump(mode="json") for event in events
                ],
            }

        return run_specialist
