from __future__ import annotations

import ast
from pathlib import Path


def test_api_disables_uvicorn_access_log_and_uses_health_aware_middleware() -> None:
    dockerfile = Path("infrastructure/api.Dockerfile").read_text(encoding="utf-8")
    main = Path("src/axiz/pe/sql_agent/main.py").read_text(encoding="utf-8")
    middleware = Path(
        "src/axiz/pe/sql_agent/core/request_logging.py"
    ).read_text(encoding="utf-8")

    assert '"--no-access-log"' in dockerfile
    assert "RequestLoggingMiddleware" in main
    assert 'not path.startswith("/health/")' in middleware
    assert "log_health_checks" in middleware


def test_logging_defaults_redact_sql_and_enable_workflow_diagnostics() -> None:
    config = Path("src/axiz/pe/sql_agent/config.py").read_text(encoding="utf-8")
    assert 'log_health_checks: bool = False' in config
    assert 'log_workflow_stages: bool = True' in config
    assert 'log_llm_calls: bool = True' in config
    assert 'log_query_events: bool = True' in config
    assert 'log_sql_text: bool = False' in config
    assert 'sse_heartbeat_seconds: float = 15.0' in config


def test_workflow_enforces_terminal_response_and_persists_terminal_message() -> None:
    source = Path("src/axiz/pe/sql_agent/workflow/service.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    function_names = {
        node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_ensure_terminal_result" in function_names
    assert "workflow_terminal_state_recovered" in source
    assert "workflow_ended_without_terminal_state" in source
    assert "agent_terminal_message_persisted" in source
    assert source.count("self._ensure_terminal_result(") >= 3


def test_sse_has_heartbeat_and_streamlit_has_bounded_reconciliation() -> None:
    route = Path("src/axiz/pe/sql_agent/api/routes/agent.py").read_text(
        encoding="utf-8"
    )
    client = Path("streamlit_app/api_client.py").read_text(encoding="utf-8")
    app = Path("streamlit_app/app.py").read_text(encoding="utf-8")

    assert 'yield ": heartbeat\\n\\n"' in route
    assert "asyncio.wait" in route
    assert "def wait_for_run" in client
    assert "recover_run=client.wait_for_run" in app
