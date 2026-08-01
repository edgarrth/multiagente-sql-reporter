from pathlib import Path

from axiz.pe.sql_agent.models.contracts import SessionTokenUsage

ROOT = Path(__file__).resolve().parents[2]


def test_session_token_usage_has_provider_reported_totals() -> None:
    usage = SessionTokenUsage()
    assert usage.model_dump() == {
        "runs": 0,
        "llm_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_output_tokens": 0,
        "by_agent": {},
    }


def test_session_repository_aggregates_usage_from_all_runs() -> None:
    source = (ROOT / "src/axiz/pe/sql_agent/repositories/session_repository.py").read_text(
        encoding="utf-8"
    )
    for key in (
        "actual_input_tokens",
        "actual_output_tokens",
        "actual_total_tokens",
        "cached_input_tokens",
        "reasoning_output_tokens",
        "by_agent",
        "jsonb_object_agg",
    ):
        assert key in source
    assert "WHERE r.session_id = :session_id" in source


def test_api_and_streamlit_expose_session_total_usage() -> None:
    route = (ROOT / "src/axiz/pe/sql_agent/api/routes/sessions.py").read_text(
        encoding="utf-8"
    )
    client = (ROOT / "streamlit_app/api_client.py").read_text(encoding="utf-8")
    ui = (ROOT / "streamlit_app/app.py").read_text(encoding="utf-8")
    usage_ui = (ROOT / "streamlit_app/ui/usage.py").read_text(encoding="utf-8")
    assert '@router.get("/{session_id}/usage"' in route
    assert "def get_session_usage" in client
    assert "render_session_usage_summary(session_usage)" in ui
    assert "Uso total de tokens de la sesi" in usage_ui
    assert "session_usage.get('total_tokens')" in usage_ui
    assert 'session_usage.get("by_agent")' in usage_ui
