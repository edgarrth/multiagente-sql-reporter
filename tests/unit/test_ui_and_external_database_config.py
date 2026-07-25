from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_streamlit_uses_chat_style_session_menu_and_clear_feedback_form() -> None:
    source = (ROOT / "streamlit_app" / "app.py").read_text(encoding="utf-8")
    assert 'st.popover("⋯")' in source
    assert 'clear_on_submit=True' in source
    assert '"＋ Nuevo chat"' in source
    assert 'delete_conversation_dialog' in source


def test_compose_allows_external_business_database() -> None:
    compose = yaml.safe_load(
        (ROOT / "infrastructure" / "docker-compose.yml").read_text(encoding="utf-8")
    )
    api_environment = compose["services"]["api"]["environment"]
    assert str(api_environment["AGENT_DATABASE_URL"]).startswith("${AGENT_DATABASE_URL:-")
    assert "host.docker.internal:host-gateway" in compose["services"]["api"]["extra_hosts"]
    postgres_health = " ".join(compose["services"]["postgres"]["healthcheck"]["test"])
    assert "axiz_agent_control" in postgres_health
    assert "axiz_business_data" not in postgres_health
