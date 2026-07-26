#!/usr/bin/env python3
"""Compare full and projected semantic context sizes without calling an LLM."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from axiz.pe.sql_agent.tools.example_selector import ExampleSelectorTool
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool
from axiz.pe.sql_agent.tools.semantic_context_projection import SemanticContextProjector


def _size(value: object) -> dict[str, int]:
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    return {
        "characters": len(serialized),
        "estimated_tokens": round(len(serialized) / 3.5),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--focus", action="append", default=[])
    parser.add_argument("--catalog", type=Path, default=ROOT / "semantic_catalog")
    args = parser.parse_args()

    catalog = SemanticCatalogTool(args.catalog)
    full = {
        "domain_definition": catalog.get_domain(args.domain)["domain"],
        "catalog_hits": catalog.search(args.question, args.domain, limit=12),
        "allowed_sources": catalog.allowed_sources(args.domain),
        "query_policy": catalog.policies(args.domain),
        "semantic_symbols": catalog.semantic_symbols(args.domain),
        "selected_examples": ExampleSelectorTool(catalog).select(
            args.question, args.domain, limit=4
        ),
    }
    projector = SemanticContextProjector()
    projected = projector.project(
        question=args.question,
        full_context=full,
        catalog_focus=args.focus,
    )
    review = projector.for_review(projected)
    report = {
        "domain": args.domain,
        "full": _size(full),
        "projected": _size(projected),
        "review": _size(review),
        "projected_ratio": round(
            _size(projected)["characters"] / max(1, _size(full)["characters"]), 4
        ),
        "review_ratio": round(
            _size(review)["characters"] / max(1, _size(full)["characters"]), 4
        ),
        "projection_metadata": projected.get("projection_metadata") or {},
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
