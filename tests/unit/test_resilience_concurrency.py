from pathlib import Path


def test_control_schema_contains_idempotency_and_execution_lease_columns() -> None:
    sql = Path("infrastructure/postgres/init/01-app-tables.sql").read_text(
        encoding="utf-8"
    )
    for fragment in (
        "idempotency_key varchar(128)",
        "version integer NOT NULL DEFAULT 0",
        "lease_owner varchar(100)",
        "lease_expires_at timestamptz",
        "heartbeat_at timestamptz",
        "cancel_requested_at timestamptz",
        "uq_agent_runs_user_idempotency",
        "uq_human_feedback_run_idempotency",
    ):
        assert fragment in sql


def test_workflow_uses_heartbeat_coordinator_and_atomic_resume_claim() -> None:
    workflow = Path("src/axiz/pe/sql_agent/workflow/service.py").read_text(
        encoding="utf-8"
    )
    repository = Path(
        "src/axiz/pe/sql_agent/repositories/run_repository.py"
    ).read_text(encoding="utf-8")

    assert "execution_coordinator.execution" in workflow
    assert "create_or_get" in workflow
    assert "claim_resume" in workflow
    assert "pg_advisory_xact_lock" in repository
    assert "status='awaiting_approval'" in repository
    assert "lease_expires_at" in repository



def test_open_revision_flow_uses_full_sql_and_generic_review() -> None:
    graph = Path("src/axiz/pe/sql_agent/workflow/graph.py").read_text(encoding="utf-8")
    nodes = Path("src/axiz/pe/sql_agent/workflow/nodes.py").read_text(encoding="utf-8")
    assert 'graph.add_edge("generate_sql", "review_revision")' in graph
    assert 'graph.add_node("prepare_requested_revision"' in graph
    assert "previous_review_sql" in nodes
    assert "raw_user_message" in nodes
    assert "SqlFeedbackApplier" not in nodes
