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


def test_session_dates_are_rendered_in_lima_timezone() -> None:
    source = (ROOT / "streamlit_app/app.py").read_text(encoding="utf-8")
    assert 'APP_TIMEZONE = ZoneInfo("America/Lima")' in source
    assert "parsed.astimezone(APP_TIMEZONE)" in source
