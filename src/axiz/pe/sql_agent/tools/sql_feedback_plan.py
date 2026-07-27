from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from axiz.pe.sql_agent.models.contracts import (
    SqlChangeType,
    SqlFeedbackPlan,
    SqlFeedbackStrategy,
)


class DeterministicFeedbackPolicy:
    """Identify feedback plans that can safely reuse a previously approved SQL contract.

    Eligibility is intentionally narrow and deterministic. The resulting SQL still passes the
    normal security, EXPLAIN/cost and HITL gates.
    """

    _SUPPORTED_TYPES = {
        SqlChangeType.SET_LIMIT,
        SqlChangeType.ADD_FILTER,
        SqlChangeType.REMOVE_FILTER,
        SqlChangeType.REPLACE_FILTER,
        SqlChangeType.CHANGE_ORDER,
    }

    @classmethod
    def is_ast_only(cls, plan: SqlFeedbackPlan | dict[str, Any] | None) -> bool:
        if not plan:
            return False
        validated = (
            plan
            if isinstance(plan, SqlFeedbackPlan)
            else SqlFeedbackPlan.model_validate(plan)
        )
        return (
            validated.strategy == SqlFeedbackStrategy.AST_ONLY
            and not validated.requires_regeneration
            and bool(validated.changes)
            and all(
                change.deterministic_candidate
                and change.change_type in cls._SUPPORTED_TYPES
                for change in validated.changes
            )
        )


class SqlFeedbackPlanValidator:
    """Normalize plan targets against the published semantic catalog.

    This prevents the interpreter from silently introducing dimensions, metrics or sources that
    are not present in the domain contract. It also derives the effective AST/regenerate/hybrid
    strategy rather than trusting the LLM to choose it.
    """

    _AST_TYPES = {
        SqlChangeType.SET_LIMIT,
        SqlChangeType.ADD_FILTER,
        SqlChangeType.REMOVE_FILTER,
        SqlChangeType.REPLACE_FILTER,
        SqlChangeType.CHANGE_ORDER,
    }
    _METRIC_TYPES = {
        SqlChangeType.ADD_METRIC,
        SqlChangeType.REMOVE_METRIC,
        SqlChangeType.REPLACE_METRIC,
    }
    _FIELD_TYPES = {
        SqlChangeType.ADD_FILTER,
        SqlChangeType.REMOVE_FILTER,
        SqlChangeType.REPLACE_FILTER,
        SqlChangeType.ADD_DIMENSION,
        SqlChangeType.REMOVE_DIMENSION,
        SqlChangeType.CHANGE_GROUPING,
    }

    def validate(
        self,
        plan: SqlFeedbackPlan,
        semantic_context: dict[str, Any],
    ) -> SqlFeedbackPlan:
        symbols = semantic_context.get("semantic_symbols") or {}
        dimensions = self._lookup(symbols.get("dimensions") or [])
        metrics = self._lookup(symbols.get("metrics") or [])
        sources = self._lookup(symbols.get("sources") or [])
        unknown: list[str] = []

        for change in plan.changes:
            target = change.target
            if not target or change.change_type in {
                SqlChangeType.SET_LIMIT,
                SqlChangeType.CHANGE_TIME_WINDOW,
                SqlChangeType.SEMANTIC_REGENERATION,
            }:
                continue
            lookup: dict[str, str]
            if change.change_type in self._METRIC_TYPES:
                lookup = metrics
            elif change.change_type == SqlChangeType.REPLACE_SOURCE:
                lookup = sources
            elif change.change_type == SqlChangeType.CHANGE_ORDER:
                lookup = {**dimensions, **metrics}
            elif change.change_type in self._FIELD_TYPES:
                lookup = dimensions
            else:
                lookup = {**dimensions, **metrics, **sources}
            canonical = lookup.get(self._key(target))
            if canonical:
                change.target = canonical
            else:
                unknown.append(target)

            if change.previous_target:
                previous = lookup.get(self._key(change.previous_target))
                if previous:
                    change.previous_target = previous

        types = {change.change_type for change in plan.changes}
        if types and types <= self._AST_TYPES:
            plan.strategy = SqlFeedbackStrategy.AST_ONLY
            plan.requires_regeneration = False
        elif types & self._AST_TYPES:
            plan.strategy = SqlFeedbackStrategy.HYBRID
            plan.requires_regeneration = True
        else:
            plan.strategy = SqlFeedbackStrategy.REGENERATE
            plan.requires_regeneration = True

        if unknown:
            unique = sorted(set(unknown))
            plan.requires_clarification = True
            plan.strategy = SqlFeedbackStrategy.CLARIFICATION
            plan.clarification_question = (
                "No pude asociar estos elementos con el catálogo semántico publicado: "
                + ", ".join(unique)
                + ". Indica una métrica o dimensión disponible del dominio."
            )
            plan.warnings.append(
                "Targets no publicados en el catálogo: " + ", ".join(unique)
            )
        return plan

    @classmethod
    def _lookup(cls, items: Iterable[dict[str, Any]]) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for item in items:
            canonical = str(item.get("column") or item.get("name") or item.get("source") or "")
            if not canonical:
                continue
            source = str(item.get("source") or "")
            candidates = {
                str(item.get("name") or ""),
                str(item.get("column") or ""),
                source,
                source.split(".")[-1] if source else "",
                *(str(value) for value in item.get("synonyms") or []),
            }
            for candidate in candidates:
                if candidate:
                    lookup[cls._key(candidate)] = canonical
        return lookup

    @staticmethod
    def _key(value: str) -> str:
        return value.strip().strip('"').lower().replace(" ", "_")
