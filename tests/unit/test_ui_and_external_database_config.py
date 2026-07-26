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
    assert api_environment["BUSINESS_DATA_MODE"] == "${BUSINESS_DATA_MODE:-embedded}"
    assert str(api_environment["AGENT_DATABASE_URL"]).startswith("${AGENT_DATABASE_URL:-")
    assert "@postgres:5432/axiz_business_data" in str(api_environment["AGENT_DATABASE_URL"])
    assert "host.docker.internal:host-gateway" in compose["services"]["api"]["extra_hosts"]
    postgres_health = " ".join(compose["services"]["postgres"]["healthcheck"]["test"])
    assert "-d postgres" in postgres_health
    assert "axiz_agent_control" not in postgres_health
    assert "postgres-bootstrap" in compose["services"]


def test_business_data_mode_defaults_to_embedded() -> None:
    from axiz.pe.sql_agent.config import Settings

    settings = Settings(_env_file=None)
    assert settings.business_data_mode == "embedded"


def test_readme_describes_embedded_poc_and_external_production() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "BUSINESS_DATA_MODE=embedded" in readme
    assert "BUSINESS_DATA_MODE=external" in readme
    assert "No se requiere ninguna base externa" in readme


def test_streamlit_exposes_conditional_excel_export() -> None:
    source = (ROOT / "streamlit_app" / "app.py").read_text(encoding="utf-8")
    client = (ROOT / "streamlit_app" / "api_client.py").read_text(encoding="utf-8")
    assert '"Preparar Excel"' in source
    assert '"⬇ Descargar Excel"' in source
    assert 'payload.get("export")' in source
    assert "/exports/excel" in client


def test_streamlit_renders_visible_security_and_cost_panel() -> None:
    source = (ROOT / "streamlit_app" / "app.py").read_text(encoding="utf-8")
    assert "def render_validation_panel" in source
    assert "Validación previa a la ejecución" in source
    assert "Controles de seguridad" in source
    assert "Evaluación de costo" in source
    assert "Plan EXPLAIN" in source
    assert 'payload.get("security_validation")' in source
    assert 'payload.get("cost_validation")' in source


def test_readme_documents_inputs_and_outputs_for_agents_and_tools() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "| Agente | Entrada | Salida | Descripción breve |" in readme
    assert "| Tool | Entrada | Salida | Descripción breve |" in readme
    assert "IntentDomainOutput" in readme
    assert "SqlGenerationOutput" in readme
    assert "SecurityValidation" in readme
    assert "CostValidation" in readme
