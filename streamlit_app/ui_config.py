from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StreamlitSettings(BaseSettings):
    """Configuration for the Streamlit client, loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    streamlit_api_base_url: str
    streamlit_run_recovery_timeout_seconds: float = Field(default=240.0, gt=0)
    streamlit_run_recovery_poll_interval_seconds: float = Field(default=0.75, gt=0)
    streamlit_http_timeout_seconds: float = Field(default=30.0, gt=0)
    streamlit_agent_http_timeout_seconds: float = Field(default=180.0, gt=0)
    streamlit_export_http_timeout_seconds: float = Field(default=60.0, gt=0)
    streamlit_sse_connect_timeout_seconds: float = Field(default=30.0, gt=0)
    streamlit_sse_write_timeout_seconds: float = Field(default=30.0, gt=0)
    streamlit_sse_pool_timeout_seconds: float = Field(default=30.0, gt=0)
    streamlit_timezone: str = "America/Lima"
    streamlit_page_title: str = "Axiz | SQL Agent"
    streamlit_page_layout: Literal["centered", "wide"] = "wide"
    streamlit_initial_sidebar_state: Literal["auto", "expanded", "collapsed"] = "expanded"
    streamlit_login_username: str = ""
    streamlit_default_session_title: str = "Nueva conversación"
    streamlit_chat_input_placeholder: str = (
        "Pregunta o solicita cualquier cambio sobre la consulta"
    )
    streamlit_auto_scroll_enabled: bool = True
    streamlit_auto_scroll_behavior: Literal["auto", "smooth"] = "auto"
    streamlit_auto_scroll_debounce_ms: int = Field(default=40, ge=0, le=2_000)
    streamlit_auto_scroll_settle_delays_ms: list[int] = Field(
        default_factory=lambda: [0, 75, 180, 400, 800]
    )

    @field_validator("streamlit_api_base_url")
    @classmethod
    def validate_api_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("STREAMLIT_API_BASE_URL must use http:// or https://")
        return normalized

    @field_validator("streamlit_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown STREAMLIT_TIMEZONE: {normalized}") from exc
        return normalized

    @field_validator("streamlit_auto_scroll_settle_delays_ms")
    @classmethod
    def validate_scroll_delays(cls, value: list[int]) -> list[int]:
        delays = sorted(set(int(delay) for delay in value if 0 <= int(delay) <= 10_000))
        return delays or [0]


@lru_cache(maxsize=1)
def get_streamlit_settings() -> StreamlitSettings:
    return StreamlitSettings()
