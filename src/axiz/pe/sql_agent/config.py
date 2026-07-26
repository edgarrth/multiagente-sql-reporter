from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Axiz SQL Agent PoC"
    app_env: str = "local"
    app_secret_key: SecretStr = SecretStr("change-me-change-me-change-me-change-me")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    bootstrap_username: str = "admin"
    bootstrap_password: SecretStr = SecretStr("Admin123!ChangeMe")
    internal_service_key: SecretStr = SecretStr("change-internal-service-key")

    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    ollama_base_url: str = "http://localhost:11434"
    ollama_api_key: SecretStr | None = None
    llm_provider: str = "openai"
    agent_models_config_path: Path = Path("config/agents.yaml")

    database_url: str = (
        "postgresql+psycopg://app_owner:app_owner@localhost:5432/axiz_agent_control"
    )
    checkpoint_database_url: str = (
        "postgresql://app_owner:app_owner@localhost:5432/axiz_agent_control"
    )
    business_data_mode: Literal["embedded", "external"] = "embedded"
    agent_database_url: SecretStr = SecretStr(
        "postgresql://agent_reader:agent_readonly@localhost:5432/axiz_business_data"
    )
    agent_database_connect_timeout_seconds: int = 10
    redis_url: str = "redis://localhost:6379/0"

    semantic_catalog_path: Path = Path("semantic_catalog")
    sql_dialect: str = "postgres"
    max_result_rows: int = 500
    max_plan_rows: int = 250_000
    max_plan_cost: float = 150_000
    max_relation_bytes: int = 512 * 1024 * 1024
    sql_timeout_seconds: int = 20
    max_sql_repair_attempts: int = 2

    excel_export_enabled: bool = True
    excel_export_max_rows: int = 5_000
    excel_export_allow_truncated: bool = False

    api_base_url: str = "http://localhost:8000"
    streamlit_api_base_url: str = "http://localhost:8000"

    teams_enabled: bool = False
    teams_port: int = 3978
    teams_bot_id: str | None = None
    teams_bot_password: SecretStr | None = None
    teams_tenant_id: str | None = None
    teams_oauth_connection_name: str | None = None

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8501"])


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
