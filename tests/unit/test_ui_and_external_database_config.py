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
    api = compose["services"]["api"]
    assert "../.env" in api["env_file"]
    assert "environment" not in api
    assert "host.docker.internal:host-gateway" in api["extra_hosts"]
    postgres_health = " ".join(compose["services"]["postgres"]["healthcheck"]["test"])
    assert "$${POSTGRES_DB}" in postgres_health
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


def test_streamlit_exposes_single_click_deferred_excel_export() -> None:
    source = (ROOT / "streamlit_app" / "app.py").read_text(encoding="utf-8")
    client = (ROOT / "streamlit_app" / "api_client.py").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"Preparar Excel"' not in source
    assert '"Exportar Excel"' in source
    assert "data=generate_excel" in source
    assert "def download_excel" in client
    assert "/exports/excel" in client
    assert 'streamlit>=1.52,<2' in pyproject


def test_streamlit_renders_visible_security_and_cost_panel() -> None:
    source = (ROOT / "streamlit_app" / "app.py").read_text(encoding="utf-8")
    assert "def render_validation_panel" in source
    assert "Validación previa a la aprobación y ejecución" in source
    assert "Controles de seguridad" in source
    assert "Evaluación de costo" in source
    assert "Plan de ejecución" in source
    assert "flatten_explain_plan" in source
    assert "No contiene las filas de negocio" in source
    assert 'payload.get("security_validation")' in source
    assert 'payload.get("cost_validation")' in source


def test_readme_documents_inputs_and_outputs_for_agents_and_tools() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "| Agente | Entrada | Salida | Descripción breve |" in readme
    assert "| Tool | Entrada | Salida | Descripción breve |" in readme
    assert "ContextResolutionOutput" in readme
    assert "AutonomousRoutingDecision" in readme
    assert "SqlGenerationOutput" in readme
    assert "SecurityValidation" in readme
    assert "CostValidation" in readme


def test_streamlit_distinguishes_actual_usage_from_approval_estimate() -> None:
    source = (ROOT / "streamlit_app" / "app.py").read_text(encoding="utf-8")
    assert "Consumo LLM ejecutado" in source
    assert "Estimación LLM si apruebas este SQL" in source
    assert "Total proyectado del run" in source


def test_graph_validates_and_estimates_before_human_review() -> None:
    source = (ROOT / "src/axiz/pe/sql_agent/workflow/graph.py").read_text(encoding="utf-8")
    assert 'graph.add_edge("generate_sql", "review_revision")' in source
    assert 'graph.add_node("prepare_requested_revision"' in source
    assert '"validate_security": "validate_security"' in source
    assert 'graph.add_edge("estimate_llm_approval", "human_review")' in source
    assert '"execute_sql": "execute_sql"' in source


def test_streamlit_uses_compact_chat_responses_and_modern_width_api() -> None:
    source = (ROOT / "streamlit_app" / "app.py").read_text(encoding="utf-8")
    usage_ui = (ROOT / "streamlit_app" / "ui" / "usage.py").read_text(encoding="utf-8")
    assert '"Qué hace esta consulta"' in source
    assert '"Detalles avanzados"' in source
    assert '"Resultado y visualización"' in source
    assert "render_compact_model_usage" in source
    assert "model-usage-line" in source
    assert 'with st.expander("Resultado y visualización", expanded=True)' in source
    assert 'with st.expander(sql_title, expanded=False)' in source
    assert 'sql_title = "SQL ejecutado"' in source
    assert 'sql_title = "SQL candidato no ejecutado"' in source
    assert "render_session_topbar" in source
    assert "HITL activo" in usage_ui
    assert "use_container_width" not in source
    assert 'width="stretch"' in source


def test_graph_has_a_non_sql_session_context_route() -> None:
    graph = (ROOT / "src/axiz/pe/sql_agent/workflow/graph.py").read_text(encoding="utf-8")
    nodes = (ROOT / "src/axiz/pe/sql_agent/workflow/nodes.py").read_text(encoding="utf-8")
    config = (ROOT / "config/agents.yaml").read_text(encoding="utf-8")
    assert 'graph.add_node("answer_conversation_context"' in graph
    assert '"conversation_question"' in nodes
    assert "investigation_coordinator:" in config
    assert "conversation_context:" not in config


def test_non_sql_answers_do_not_render_query_specific_sections() -> None:
    source = (ROOT / "streamlit_app" / "app.py").read_text(encoding="utf-8")
    assert "if not sql:" in source
    assert "They must not display SQL-specific explanations" in source
