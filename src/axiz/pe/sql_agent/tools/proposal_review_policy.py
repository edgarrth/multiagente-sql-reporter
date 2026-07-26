from __future__ import annotations

from typing import Any

from axiz.pe.sql_agent.models.contracts import ProposalReviewDecision

try:
    import sqlglot
    from sqlglot import exp
except Exception:  # pragma: no cover - optional in lightweight static test environments
    sqlglot = None
    exp = None


class ProposalReviewPolicy:
    """Decide deterministically whether an extra LLM self-review is worth its cost.

    This is a risk router, not an approval gate. Security, cost and HITL always run.
    """

    def __init__(
        self,
        dialect: str,
        *,
        high_cost_ratio: float = 0.70,
        high_row_ratio: float = 0.70,
    ) -> None:
        self.dialect = dialect
        self.high_cost_ratio = high_cost_ratio
        self.high_row_ratio = high_row_ratio

    @staticmethod
    def _ratio(value: float | int | None, maximum: float | int | None) -> float:
        if value is None or not maximum:
            return 0.0
        return float(value) / float(maximum)

    def evaluate(
        self,
        *,
        task: dict[str, Any],
        generated_contract: dict[str, Any],
        final_sql: str,
        semantic_context: dict[str, Any],
        security_validation: dict[str, Any],
        cost_validation: dict[str, Any],
        feedback_plan: dict[str, Any] | None = None,
    ) -> ProposalReviewDecision:
        reasons: list[str] = []
        checks: list[str] = [
            "security_approved",
            "cost_approved",
            "catalog_symbols_projected",
        ]
        sources = list(generated_contract.get("source_objects") or [])
        assumptions = list(generated_contract.get("assumptions") or [])
        feedback_plan = feedback_plan or {}

        if len(sources) > 1:
            reasons.append("multiple_sources")
        if assumptions:
            reasons.append("semantic_assumptions")

        # Multiple requested KPIs are common and can still be satisfied by one governed query.
        # Escalate only when the generated contract uses unpublished sources or symbols.
        allowed_sources = set(semantic_context.get("allowed_sources") or [])
        if allowed_sources and any(source not in allowed_sources for source in sources):
            reasons.append("source_not_in_semantic_allowlist")
        symbols = dict(semantic_context.get("semantic_symbols") or {})
        published_metrics = {
            str(item.get("name") or item.get("column") or "")
            for item in (symbols.get("metrics") or [])
        }
        selected_metrics = {str(item) for item in generated_contract.get("selected_metrics") or []}
        if published_metrics and selected_metrics - published_metrics:
            reasons.append("unpublished_metric")
        published_dimensions = {
            str(item.get("name") or item.get("column") or "")
            for item in (symbols.get("dimensions") or [])
        }
        selected_dimensions = {
            str(item) for item in generated_contract.get("selected_dimensions") or []
        }
        if published_dimensions and selected_dimensions - published_dimensions:
            reasons.append("unpublished_dimension")
        if feedback_plan.get("requires_regeneration") or feedback_plan.get("strategy") in {
            "regenerate",
            "hybrid",
        }:
            reasons.append("semantic_revision")

        cost_ratio = self._ratio(
            cost_validation.get("total_cost"),
            cost_validation.get("max_plan_cost"),
        )
        row_ratio = self._ratio(
            cost_validation.get("max_node_rows") or cost_validation.get("plan_rows"),
            cost_validation.get("max_plan_rows"),
        )
        if cost_ratio >= self.high_cost_ratio:
            reasons.append("high_plan_cost")
        if row_ratio >= self.high_row_ratio:
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
                if tree.args.get("having") is not None:
                    reasons.append("having")
            except Exception:
                reasons.append("unparsed_for_risk_review")
        else:
            upper = final_sql.upper()
            if " JOIN " in upper:
                reasons.append("join")
            if " WITH " in f" {upper}" or " OVER (" in upper or " HAVING " in upper:
                reasons.append("complex_sql_shape")

        projected = dict(semantic_context.get("projection_metadata") or {})
        if not projected.get("fingerprint"):
            reasons.append("unversioned_semantic_context")

        return ProposalReviewDecision(
            requires_llm_review=bool(reasons),
            reasons=sorted(set(reasons)),
            checks=checks,
        )
