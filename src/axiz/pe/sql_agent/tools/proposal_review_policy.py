from __future__ import annotations

from typing import Any

from axiz.pe.sql_agent.models.contracts import ProposalReviewDecision

try:
    import sqlglot
    from sqlglot import exp
except Exception:  # pragma: no cover
    sqlglot = None
    exp = None


class ProposalReviewPolicy:
    """Route high-risk SQL proposals to an extra semantic review.

    The policy inspects generic SQL structure, cost, assumptions, and source provenance. It does
    not maintain a fixed list of metrics, dimensions, filters, or feedback types.
    """

    def __init__(self, dialect: str, *, high_cost_ratio: float = 0.70, high_row_ratio: float = 0.70) -> None:
        self.dialect = dialect
        self.high_cost_ratio = high_cost_ratio
        self.high_row_ratio = high_row_ratio

    @staticmethod
    def _ratio(value: float | int | None, maximum: float | int | None) -> float:
        return float(value) / float(maximum) if value is not None and maximum else 0.0

    def evaluate(
        self,
        *,
        task: dict[str, Any],
        generated_output: dict[str, Any],
        final_sql: str,
        semantic_context: dict[str, Any],
        security_validation: dict[str, Any],
        cost_validation: dict[str, Any],
        **_: Any,
    ) -> ProposalReviewDecision:
        reasons: list[str] = []
        sources = list(generated_output.get("source_objects") or [])
        assumptions = list(generated_output.get("assumptions") or [])
        allowed_sources = set(semantic_context.get("allowed_sources") or [])
        if len(sources) > 1:
            reasons.append("multiple_sources")
        if assumptions:
            reasons.append("semantic_assumptions")
        if allowed_sources and any(source not in allowed_sources for source in sources):
            reasons.append("source_not_in_semantic_allowlist")
        cost_ratio = self._ratio(
            cost_validation.get("total_cost"),
            cost_validation.get("max_plan_cost"),
        )
        if cost_ratio >= self.high_cost_ratio:
            reasons.append("high_plan_cost")
        if self._ratio(
            cost_validation.get("max_node_rows") or cost_validation.get("plan_rows"),
            cost_validation.get("max_plan_rows"),
        ) >= self.high_row_ratio:
            reasons.append("high_plan_rows")
        if sqlglot is not None:
            try:
                tree = sqlglot.parse_one(final_sql, read=self.dialect)
                if any(tree.find_all(exp.Join)):
                    reasons.append("join")
                if any(tree.find_all(exp.Subquery)) or any(tree.find_all(exp.CTE)):
                    reasons.append("nested_query")
                if any(tree.find_all(exp.Window)):
                    reasons.append("window_function")
                if any(tree.find_all(exp.Having)):
                    reasons.append("having")
            except Exception:
                reasons.append("unparsed_for_risk_review")
        return ProposalReviewDecision(
            requires_llm_review=bool(reasons),
            reasons=sorted(set(reasons)),
            checks=["security_approved", "cost_approved", "catalog_sources_projected"],
        )
