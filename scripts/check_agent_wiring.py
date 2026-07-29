#!/usr/bin/env python3
"""Validate that application composition only uses public agent interfaces."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTAINER = ROOT / "src/axiz/pe/sql_agent/container.py"
SQL_AGENT = ROOT / "src/axiz/pe/sql_agent/agents/sql_engineer_agent.py"
EVIDENCE_AGENT = ROOT / "src/axiz/pe/sql_agent/agents/evidence_reviewer_agent.py"


def public_async_methods(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not child.name.startswith("_")
            }
    raise SystemExit(f"Class {class_name} not found in {path}")


def main() -> None:
    source = CONTAINER.read_text(encoding="utf-8")
    forbidden = [
        "sql_engineer_agent.generator",
        "sql_engineer_agent.revision_interpreter",
        "evidence_reviewer_agent.verifier",
        "evidence_reviewer_agent.explainer",
        "evidence_reviewer_agent.critic",
    ]
    found = [item for item in forbidden if item in source]
    if found:
        raise SystemExit("Removed component aliases referenced by container: " + ", ".join(found))

    sql_methods = public_async_methods(SQL_AGENT, "SqlEngineerAgent")
    missing_sql = {"generate", "review_revision", "validate"} - sql_methods
    if missing_sql:
        raise SystemExit("SqlEngineerAgent public contract missing: " + ", ".join(sorted(missing_sql)))

    evidence_methods = public_async_methods(EVIDENCE_AGENT, "EvidenceReviewerAgent")
    missing_evidence = {"verify", "explain", "review"} - evidence_methods
    if missing_evidence:
        raise SystemExit(
            "EvidenceReviewerAgent public contract missing: "
            + ", ".join(sorted(missing_evidence))
        )

    if "sql_agent=self.sql_engineer_agent" not in source:
        raise SystemExit("SpecialistSubgraphFactory must receive SqlEngineerAgent directly")
    if "EvidenceReviewSubgraphFactory(self.evidence_reviewer_agent)" not in source:
        raise SystemExit("EvidenceReviewSubgraphFactory must receive EvidenceReviewerAgent directly")

    print("Agent wiring validation passed.")


if __name__ == "__main__":
    main()
