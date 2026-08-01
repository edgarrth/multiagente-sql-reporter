from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_ui_remains_streamlit_and_does_not_copy_angular_runtime() -> None:
    source = (ROOT / "streamlit_app/app.py").read_text(encoding="utf-8")
    assert "import streamlit as st" in source
    assert "st.chat_input" in source
    assert not (ROOT / "frontend").exists()
    assert not (ROOT / "angular.json").exists()
    assert not (ROOT / "package.json").exists()


def test_streamlit_uses_reference_inspired_dark_chat_shell() -> None:
    source = (ROOT / "streamlit_app/app.py").read_text(encoding="utf-8")
    for token in (
        "--axiz-bg:#081018",
        "axiz-topbar",
        "axiz-usage",
        "axiz-header-usage",
        "stDeployButton",
        "stChatInput",
        "stBottomBlockContainer",
        "textarea::placeholder",
        "stChatMessage",
        "＋ Nuevo chat",
    ):
        assert token in source


def test_streamlit_theme_matches_dark_chat_shell() -> None:
    theme = (ROOT / ".streamlit/config.toml").read_text(encoding="utf-8")
    assert 'base = "dark"' in theme
    assert 'primaryColor = "#43C3EC"' in theme
    assert 'backgroundColor = "#081018"' in theme
    assert 'secondaryBackgroundColor = "#0B1721"' in theme


def test_trace_detail_is_escaped_before_unsafe_html_rendering() -> None:
    source = (ROOT / "streamlit_app/app.py").read_text(encoding="utf-8")
    assert "escape(str(step['detail']))" in source


def test_streamlit_header_replaces_native_deploy_controls_with_usage_banner() -> None:
    source = (ROOT / "streamlit_app/app.py").read_text(encoding="utf-8")
    usage_ui = (ROOT / "streamlit_app/ui/usage.py").read_text(encoding="utf-8")
    assert "render_header_usage_banner(session_usage)" in source
    assert "Costo estimado (usd)" in usage_ui
    assert "AXIZ_LLM_INPUT_USD_PER_1K" in usage_ui
    assert "AXIZ_LLM_OUTPUT_USD_PER_1K" in usage_ui


def test_session_dates_are_rendered_in_lima_timezone() -> None:
    source = (ROOT / "streamlit_app/app.py").read_text(encoding="utf-8")
    assert "APP_TIMEZONE = ZoneInfo(UI_SETTINGS.streamlit_timezone)" in source
    assert "parsed.astimezone(APP_TIMEZONE)" in source


def test_streamlit_installs_persistent_chat_autoscroll() -> None:
    source = (ROOT / "streamlit_app/app.py").read_text(encoding="utf-8")
    config = (ROOT / "streamlit_app/ui_config.py").read_text(encoding="utf-8")
    assert "def install_auto_scroll" in source
    assert "MutationObserver" in source
    assert "stChatMessage" in source
    assert "answer_delta" in source
    assert "install_auto_scroll()" in source
    assert "streamlit_auto_scroll_enabled" in config
    assert "streamlit_auto_scroll_settle_delays_ms" in config
