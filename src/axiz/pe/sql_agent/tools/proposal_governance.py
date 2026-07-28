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
            detail = SpecialistProposalGovernance._validation_detail(
                security_validation,
                fallback="The proposal did not pass the current SQL security and governance validation",
            )
            return SpecialistProposalGateDecision(
                SpecialistProposalStatus.BLOCKED,
                detail,
            )
        if not bool(cost_validation.get("approved")):
            detail = SpecialistProposalGovernance._validation_detail(
                cost_validation,
                fallback="The proposal did not pass the current query-cost validation",
            )
            return SpecialistProposalGateDecision(
                SpecialistProposalStatus.BLOCKED,
                detail,
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

    @staticmethod
    def _validation_detail(payload: dict[str, Any], *, fallback: str) -> str:
        """Return a bounded, actionable deterministic-gate message."""
        candidates: list[str] = []
        error_message = payload.get("error_message")
        if error_message:
            candidates.append(str(error_message))
        for key in ("violations", "warnings"):
            values = payload.get(key) or []
            if isinstance(values, list):
                candidates.extend(str(value) for value in values if value)
        detail = "; ".join(
            dict.fromkeys(value.strip() for value in candidates if value.strip())
        )
        if not detail:
            return fallback
        return f"{fallback}: {detail}"[:700]
