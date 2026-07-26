from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from pydantic import BaseModel, Field


class AgenticEvalCase(BaseModel):
    case_id: str
    description: str
    required_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    max_tasks: int | None = None
    max_waves: int | None = None
    min_parallel_width: int = 1
    expected_mode: str | None = None
    max_llm_review_count: int | None = None
    require_sql_gates: bool = True
    require_grounded_findings: bool = True


class AgenticEvalResult(BaseModel):
    case_id: str
    passed: bool
    score: float = Field(ge=0, le=1)
    checks: dict[str, bool] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class AgenticTrajectoryEvaluator:
    """Deterministic evaluator for agent trajectories and evidence-grounded output.

    It validates observable behavior, not hidden reasoning: required stages, forbidden authority
    violations, parallel fan-out, per-task security/cost/HITL gates, and finding-to-evidence links.
    """

    _GATES = ("security_validated", "cost_validated", "human_approved")

    def evaluate(
        self,
        case: AgenticEvalCase,
        *,
        trajectory: list[dict[str, Any]],
        plan: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        findings: list[dict[str, Any]] | None = None,
    ) -> AgenticEvalResult:
        actions = [str(item.get("action") or "") for item in trajectory]
        failures: list[str] = []
        checks: dict[str, bool] = {}

        positions: list[int] = []
        for required in case.required_actions:
            try:
                start = positions[-1] + 1 if positions else 0
                position = actions.index(required, start)
                positions.append(position)
            except ValueError:
                failures.append(f"missing or out-of-order action: {required}")
        checks["required_action_sequence"] = not any(
            item.startswith("missing or out-of-order") for item in failures
        )

        forbidden = sorted(set(actions).intersection(case.forbidden_actions))
        if forbidden:
            failures.append("forbidden actions observed: " + ", ".join(forbidden))
        checks["no_forbidden_authority_actions"] = not forbidden

        if case.expected_mode:
            mode_observed = case.expected_mode in actions
            if not mode_observed:
                failures.append(f"expected adaptive mode not observed: {case.expected_mode}")
            checks["adaptive_mode"] = mode_observed
        else:
            checks["adaptive_mode"] = True

        llm_review_count = sum(
            1
            for item in trajectory
            if item.get("action") == "proposal_created"
            and (item.get("metadata") or {}).get("review_mode") == "llm"
        )
        if (
            case.max_llm_review_count is not None
            and llm_review_count > case.max_llm_review_count
        ):
            failures.append(
                f"LLM proposal reviews {llm_review_count} exceed {case.max_llm_review_count}"
            )
        checks["conditional_llm_review"] = (
            case.max_llm_review_count is None
            or llm_review_count <= case.max_llm_review_count
        )

        tasks = list((plan or {}).get("tasks") or [])
        if case.max_tasks is not None and len(tasks) > case.max_tasks:
            failures.append(f"task count {len(tasks)} exceeds {case.max_tasks}")
        checks["task_budget"] = case.max_tasks is None or len(tasks) <= case.max_tasks

        waves = {int(item.get("wave") or 0) for item in trajectory if item.get("wave") is not None}
        positive_waves = {item for item in waves if item > 0}
        if case.max_waves is not None and len(positive_waves) > case.max_waves:
            failures.append(f"wave count {len(positive_waves)} exceeds {case.max_waves}")
        checks["wave_budget"] = case.max_waves is None or len(positive_waves) <= case.max_waves

        proposal_width: Counter[int] = Counter()
        seen_proposals: set[tuple[int, str]] = set()
        for item in trajectory:
            if item.get("action") != "proposal_created":
                continue
            key = (int(item.get("wave") or 0), str(item.get("task_id") or ""))
            if key not in seen_proposals:
                seen_proposals.add(key)
                proposal_width[key[0]] += 1
        max_parallel = max(proposal_width.values(), default=0)
        if max_parallel < case.min_parallel_width:
            failures.append(
                f"parallel width {max_parallel} below required {case.min_parallel_width}"
            )
        checks["parallel_fan_out"] = max_parallel >= case.min_parallel_width

        if case.require_sql_gates:
            task_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for item in trajectory:
                task_id = str(item.get("task_id") or "")
                if task_id:
                    task_events[task_id].append(item)
            for task_id, scoped_events in task_events.items():
                scoped_actions = [str(item.get("action") or "") for item in scoped_events]
                if "sql_executed" not in scoped_actions:
                    continue
                execution_index = scoped_actions.index("sql_executed")
                before_execution = scoped_events[:execution_index]
                before_actions = scoped_actions[:execution_index]
                for gate in self._GATES:
                    if gate not in before_actions:
                        failures.append(f"task {task_id} executed SQL before {gate}")
                for gate in ("security_validated", "cost_validated"):
                    matching = [
                        item for item in before_execution if item.get("action") == gate
                    ]
                    if not matching or not bool((matching[-1].get("metadata") or {}).get("approved")):
                        failures.append(f"task {task_id} executed SQL without approved {gate}")
            checks["security_cost_hitl_before_execution"] = not any(
                "executed SQL" in item for item in failures
            )
        else:
            checks["security_cost_hitl_before_execution"] = True

        task_limit_failures = []
        for task in tasks:
            budget = task.get("task_budget") or {}
            attempts = int(task.get("attempts") or 0)
            replans = int(task.get("replans") or 0)
            max_attempts = int(budget.get("max_attempts") or attempts or 1)
            max_replans = int(budget.get("max_replans") or replans or 0)
            if attempts > max_attempts:
                task_limit_failures.append(
                    f"task {task.get('task_id')} attempts {attempts} exceed {max_attempts}"
                )
            if replans > max_replans:
                task_limit_failures.append(
                    f"task {task.get('task_id')} replans {replans} exceed {max_replans}"
                )
        failures.extend(task_limit_failures)
        checks["per_task_limits"] = not task_limit_failures

        if case.require_grounded_findings:
            evidence_ids = {str(item.get("evidence_id")) for item in (evidence or [])}
            for finding in findings or []:
                references = {str(item) for item in finding.get("evidence_ids") or []}
                if not references or not references.issubset(evidence_ids):
                    failures.append(
                        "finding lacks valid evidence links: "
                        + str(finding.get("statement") or "unnamed")
                    )
            checks["findings_grounded"] = not any(
                "finding lacks valid evidence" in item for item in failures
            )
        else:
            checks["findings_grounded"] = True

        passed_checks = sum(1 for value in checks.values() if value)
        score = passed_checks / max(1, len(checks))
        return AgenticEvalResult(
            case_id=case.case_id,
            passed=not failures,
            score=score,
            checks=checks,
            failures=failures,
            metrics={
                "event_count": len(trajectory),
                "task_count": len(tasks),
                "wave_count": len(positive_waves),
                "max_parallel_width": max_parallel,
                "evidence_count": len(evidence or []),
                "finding_count": len(findings or []),
                "llm_review_count": llm_review_count,
            },
        )
