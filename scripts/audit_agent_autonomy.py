#!/usr/bin/env python3
"""Reject closed feedback taxonomies and universal analytical query-shape rules."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_SCOPES = (
    ROOT / "src/axiz/pe/sql_agent/agents",
    ROOT / "src/axiz/pe/sql_agent/skills",
    ROOT / "src/axiz/pe/sql_agent/workflow",
)
POLICY_SCOPES = (
    ROOT / "config",
    ROOT / "semantic_catalog",
)
CODE_FORBIDDEN = {
    "SqlFeedbackPlan": "closed feedback plan",
    "SqlFeedbackApplier": "hard-coded feedback applier",
    "time_window_delta_months": "fixed temporal feedback property",
    "required_filter_columns": "mandatory filter checklist",
    "enforce_temporal_filter": "mandatory temporal clause policy",
    "_MONTH_INTERVAL": "regex-based month intent parsing",
    "_MONTH_DELTA_PATTERNS": "regex-based month intent parsing",
    "feedback_plan": "legacy fixed feedback state",
    "selected_metrics": "legacy fixed semantic property",
    "selected_dimensions": "legacy fixed semantic property",
    "selected_filters": "legacy fixed semantic property",
}
POLICY_FORBIDDEN = {
    "default_date_column": "universal default date member",
    "temporal_filter_columns": "fixed temporal filter list",
    "required_filter_columns": "mandatory filter checklist",
    "enforce_temporal_filter": "mandatory temporal clause policy",
    "allow_ordered_top_n_without_time_filter": "special-cased top-N policy",
    "Prefer closed calendar periods": "universal closed-period preference",
    "Include an explicit lower and upper date boundary for transactional views": (
        "universal transaction date requirement"
    ),
}


def _scan(scopes: tuple[Path, ...], forbidden: dict[str, str]) -> list[str]:
    violations: list[str] = []
    for scope in scopes:
        for path in sorted(item for item in scope.rglob("*") if item.is_file()):
            if path.suffix not in {".py", ".yaml", ".yml"}:
                continue
            source = path.read_text(encoding="utf-8")
            for token, reason in forbidden.items():
                if token in source:
                    violations.append(f"{path.relative_to(ROOT)}: {token} ({reason})")
    return violations


def main() -> int:
    violations = _scan(CODE_SCOPES, CODE_FORBIDDEN)
    violations.extend(_scan(POLICY_SCOPES, POLICY_FORBIDDEN))
    if violations:
        print("Autonomy audit failed:")
        print("\n".join(f"- {item}" for item in violations))
        return 1
    print(
        "Autonomy audit passed: no closed feedback taxonomy or universal query shape found."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
