#!/usr/bin/env python3
"""Live E2E harness for a running stack.

It starts cases through the real API, automatically approves each governed HITL proposal, waits
for completion, and writes RunResponse JSON files that can be evaluated with run_agentic_evals.py.
This script is intentionally not part of the offline unit suite because it consumes configured LLM
and database resources.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--output", type=Path, default=Path("agentic-eval-run.json"))
    args = parser.parse_args()
    client = httpx.Client(base_url=args.base_url, timeout=180)
    token = client.post(
        "/api/v1/auth/login", json={"username": args.username, "password": args.password}
    ).raise_for_status().json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    session = client.post("/api/v1/sessions", json={}, headers=headers).raise_for_status().json()
    response = client.post(
        "/api/v1/agent/runs",
        json={"session_id": session["id"], "question": args.question},
        headers={**headers, "Idempotency-Key": str(uuid4())},
    ).raise_for_status().json()
    while response.get("status") == "awaiting_approval":
        response = client.post(
            f"/api/v1/agent/runs/{response['run_id']}/feedback",
            json={"decision": "approve", "comment": "Approved by live agentic eval"},
            headers={**headers, "Idempotency-Key": str(uuid4())},
        ).raise_for_status().json()
    for _ in range(60):
        if response.get("status") in {"completed", "failed", "rejected", "cancelled"}:
            break
        time.sleep(1)
        response = client.get(
            f"/api/v1/agent/runs/{response['run_id']}", headers=headers
        ).raise_for_status().json()
    args.output.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0 if response.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
