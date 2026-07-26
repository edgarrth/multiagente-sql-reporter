from pathlib import Path

import yaml

from axiz.pe.sql_agent.evals import AgenticEvalCase, AgenticTrajectoryEvaluator

ROOT = Path(__file__).resolve().parents[2]


def _event(action: str, *, task: str | None = None, wave: int | None = None, cache=False):
    metadata = {"approved": True} if action in {"security_validated", "cost_validated"} else {}
    return {
        "sequence": 1,
        "stage": "eval",
        "actor": "test",
        "action": action,
        "task_id": task,
        "wave": wave,
        "cache_hit": cache,
        "metadata": metadata,
    }


def _cases() -> dict[str, AgenticEvalCase]:
    payload = yaml.safe_load(
        (ROOT / "datasets/evals/autonomous_society.yaml").read_text(encoding="utf-8")
    )
    return {
        item["case_id"]: AgenticEvalCase.model_validate(item)
        for item in payload["cases"]
    }


def test_simple_governed_trajectory_passes_end_to_end_contract() -> None:
    events = [
        _event("delegate", wave=1),
        _event("security_validated", task="t1", wave=1),
        _event("cost_validated", task="t1", wave=1),
        _event("proposal_created", task="t1", wave=1),
        _event("proposal_selected_for_hitl", task="t1", wave=1),
        _event("human_approved", task="t1", wave=1),
        _event("sql_executed", task="t1", wave=1),
        _event("evidence_recorded", task="t1", wave=1),
        _event("evidence_reviewed"),
        _event("investigation_finalized"),
    ]
    result = AgenticTrajectoryEvaluator().evaluate(
        _cases()["simple_governed_query"],
        trajectory=events,
        plan={"tasks": [{"task_id": "t1"}]},
        evidence=[{"evidence_id": "e1"}],
        findings=[{"statement": "Hallazgo", "evidence_ids": ["e1"]}],
    )
    assert result.passed, result.failures
    assert result.score == 1.0


def test_parallel_wave_is_observable_and_grounded() -> None:
    events = [
        _event("delegate", wave=1),
        _event("security_validated", task="a", wave=1),
        _event("cost_validated", task="a", wave=1),
        _event("proposal_created", task="a", wave=1),
        _event("security_validated", task="b", wave=1),
        _event("cost_validated", task="b", wave=1),
        _event("proposal_created", task="b", wave=1),
        _event("human_approved", task="a", wave=1),
        _event("sql_executed", task="a", wave=1),
        _event("human_approved", task="b", wave=1),
        _event("sql_executed", task="b", wave=1),
        _event("evidence_reviewed"),
        _event("investigation_finalized"),
    ]
    result = AgenticTrajectoryEvaluator().evaluate(
        _cases()["parallel_cross_evidence"],
        trajectory=events,
        plan={"tasks": [{"task_id": "a"}, {"task_id": "b"}]},
        evidence=[{"evidence_id": "e1"}, {"evidence_id": "e2"}],
        findings=[{"statement": "Comparación", "evidence_ids": ["e1", "e2"]}],
    )
    assert result.passed, result.failures
    assert result.metrics["max_parallel_width"] == 2


def test_evaluator_fails_when_sql_bypasses_hitl_or_finding_is_unlinked() -> None:
    case = _cases()["simple_governed_query"].model_copy(update={"required_actions": []})
    result = AgenticTrajectoryEvaluator().evaluate(
        case,
        trajectory=[
            _event("security_validated", task="t1", wave=1),
            _event("cost_validated", task="t1", wave=1),
            _event("proposal_created", task="t1", wave=1),
            _event("sql_executed", task="t1", wave=1),
        ],
        plan={"tasks": [{"task_id": "t1"}]},
        evidence=[{"evidence_id": "e1"}],
        findings=[{"statement": "Sin sustento", "evidence_ids": ["missing"]}],
    )
    assert result.passed is False
    assert any("human_approved" in item for item in result.failures)
    assert any("valid evidence" in item for item in result.failures)


def test_cached_proposal_is_revalidated_before_execution() -> None:
    events = [
        _event("security_validated", task="t1", wave=1, cache=True),
        _event("cost_validated", task="t1", wave=1, cache=True),
        _event("proposal_created", task="t1", wave=1, cache=True),
        _event("human_approved", task="t1", wave=1, cache=True),
        _event("sql_executed", task="t1", wave=1, cache=True),
    ]
    result = AgenticTrajectoryEvaluator().evaluate(
        _cases()["cached_proposal_is_revalidated"],
        trajectory=events,
        plan={
            "tasks": [
                {
                    "task_id": "t1",
                    "attempts": 1,
                    "replans": 0,
                    "task_budget": {"max_attempts": 2, "max_replans": 1},
                }
            ]
        },
        evidence=[{"evidence_id": "e1"}],
        findings=[{"statement": "Cache validado", "evidence_ids": ["e1"]}],
    )
    assert result.passed, result.failures


def test_evaluator_rejects_failed_security_metadata_and_task_budget_overrun() -> None:
    events = [
        {**_event("security_validated", task="t1", wave=1), "metadata": {"approved": False}},
        _event("cost_validated", task="t1", wave=1),
        _event("human_approved", task="t1", wave=1),
        _event("sql_executed", task="t1", wave=1),
    ]
    result = AgenticTrajectoryEvaluator().evaluate(
        _cases()["simple_governed_query"].model_copy(update={"required_actions": []}),
        trajectory=events,
        plan={
            "tasks": [
                {
                    "task_id": "t1",
                    "attempts": 3,
                    "replans": 2,
                    "task_budget": {"max_attempts": 2, "max_replans": 1},
                }
            ]
        },
        evidence=[{"evidence_id": "e1"}],
        findings=[{"statement": "Hallazgo", "evidence_ids": ["e1"]}],
    )
    assert result.passed is False
    assert any("approved security_validated" in item for item in result.failures)
    assert any("attempts" in item for item in result.failures)
    assert any("replans" in item for item in result.failures)
