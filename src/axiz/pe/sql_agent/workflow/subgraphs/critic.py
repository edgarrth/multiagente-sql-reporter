from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from axiz.pe.sql_agent.agents.autonomous.critic_agent import CriticAgent
from axiz.pe.sql_agent.models.contracts import (
    AutonomousBudgetUsage,
    CriticReviewOutput,
    InvestigationPlan,
)


class CriticSubgraphState(TypedDict, total=False):
    question: str
    plan: dict[str, Any]
    evidence: list[dict[str, Any]]
    budget_remaining: dict[str, Any]
    available_specialists: list[dict[str, Any]]
    review: dict[str, Any]
    validation_errors: list[str]


class CriticSubgraphFactory:
    def __init__(self, critic_agent: CriticAgent) -> None:
        self.critic_agent = critic_agent

    def build(self):
        async def review(state: CriticSubgraphState) -> CriticSubgraphState:
            output = await self.critic_agent.review(
                question=state["question"],
                plan=InvestigationPlan.model_validate(state["plan"]),
                evidence=list(state.get("evidence") or []),
                budget_remaining=dict(state.get("budget_remaining") or {}),
                available_specialists=list(state.get("available_specialists") or []),
            )
            return {"review": output.model_dump(mode="json")}

        def validate(state: CriticSubgraphState) -> CriticSubgraphState:
            output = CriticReviewOutput.model_validate(state["review"])
            evidence_ids = {str(item.get("evidence_id")) for item in state.get("evidence") or []}
            errors: list[str] = []
            unknown = set(output.accepted_evidence_ids) - evidence_ids
            if unknown:
                errors.append("critic referenced unknown evidence: " + ", ".join(sorted(unknown)))
            if output.ready_to_finalize and not output.accepted_evidence_ids:
                errors.append("critic cannot finalize without accepted evidence")
            if errors:
                output = output.model_copy(
                    update={
                        "ready_to_finalize": False,
                        "rationale": (output.rationale + " " + "; ".join(errors)).strip(),
                    }
                )
            return {
                "review": output.model_dump(mode="json"),
                "validation_errors": errors,
            }

        graph = StateGraph(CriticSubgraphState)
        graph.add_node("review", review)
        graph.add_node("validate", validate)
        graph.add_edge(START, "review")
        graph.add_edge("review", "validate")
        graph.add_edge("validate", END)
        return graph.compile()
