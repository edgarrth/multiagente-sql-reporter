#!/usr/bin/env python3
"""Evaluate a persisted RunResponse JSON or a directory of JSON responses offline."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from axiz.pe.sql_agent.evals import AgenticEvalCase, AgenticTrajectoryEvaluator


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("response", type=Path, help="RunResponse JSON file")
    parser.add_argument("--case", required=True)
    parser.add_argument(
        "--dataset", type=Path, default=Path("datasets/evals/autonomous_society.yaml")
    )
    args = parser.parse_args()
    dataset = yaml.safe_load(args.dataset.read_text(encoding="utf-8"))
    cases = {
        item["case_id"]: AgenticEvalCase.model_validate(item)
        for item in dataset.get("cases") or []
    }
    if args.case not in cases:
        raise SystemExit(f"Unknown eval case: {args.case}")
    payload = json.loads(args.response.read_text(encoding="utf-8"))
    investigation = payload.get("autonomous_investigation") or {}
    result = AgenticTrajectoryEvaluator().evaluate(
        cases[args.case],
        trajectory=investigation.get("trajectory") or [],
        plan=investigation.get("plan") or {},
        evidence=investigation.get("evidence") or [],
        findings=investigation.get("findings") or [],
    )
    print(result.model_dump_json(indent=2))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
