from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from axiz.pe.sql_agent.models.contracts import (
    SpecialistProposalReview,
    SpecialistProposalStatus,
)


@dataclass(frozen=True)
class SpecialistProposalGateDecision:
    status: SpecialistProposalStatus
    block_reason: str | None = None


class SpecialistProposalGovernance:
    """Deterministic gate for specialist proposals.

    Cache provenance never grants approval. A proposal is eligible for HITL only when task budget,
    SQL security, cost policy and specialist self-review all passed in the current invocation.
    """

    @staticmethod
    def evaluate(
        *,
        error: str | None,
        cache_hit: bool,
        security_validation: dict[str, Any],
        cost_validation: dict[str, Any],
        review: SpecialistProposalReview | None,
        task_budget_approved: bool,
        task_budget_violations: list[str] | None = None,
    ) -> SpecialistProposalGateDecision:
        if error:
            return SpecialistProposalGateDecision(
                SpecialistProposalStatus.BLOCKED,
                error,
            )
        if not task_budget_approved:
            return SpecialistProposalGateDecision(
                SpecialistProposalStatus.BLOCKED,
                "Task budget exhausted: " + ", ".join(task_budget_violations or []),
            )
        if not bool(security_validation.get("approved")):
            return SpecialistProposalGateDecision(
                SpecialistProposalStatus.BLOCKED,
                "The proposal did not pass the current SQL security validation",
            )
        if not bool(cost_validation.get("approved")):
            return SpecialistProposalGateDecision(
                SpecialistProposalStatus.BLOCKED,
                "The proposal did not pass the current query-cost validation",
            )
        if review is None:
            return SpecialistProposalGateDecision(
                SpecialistProposalStatus.FAILED,
                "The proposal was not reviewed by its specialist",
            )
        if not review.approved:
            return SpecialistProposalGateDecision(
                SpecialistProposalStatus.FAILED,
                review.retry_instruction or "The specialist rejected its own proposal",
            )
        return SpecialistProposalGateDecision(
            SpecialistProposalStatus.CACHE_HIT if cache_hit else SpecialistProposalStatus.READY,
            None,
        )
