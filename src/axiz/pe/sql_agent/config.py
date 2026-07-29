from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or ``.env``.

    Security-sensitive values and connection strings intentionally have no source-code
    defaults. The application must receive them from the deployment environment, a local
    ``.env`` file, or a secrets manager exposed as environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Axiz SQL Agent PoC"
    app_env: str = "local"
    app_secret_key: SecretStr
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    bootstrap_username: str
    bootstrap_password: SecretStr
    bootstrap_roles: list[str]
    bootstrap_sync_credentials: bool = False
    internal_service_key: SecretStr

    # Structured observability. Sensitive prompts/SQL remain redacted by default.
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    log_http_requests: bool = True
    log_health_checks: bool = False
    log_workflow_stages: bool = True
    log_llm_calls: bool = True
    log_query_events: bool = True
    log_sql_text: bool = False
    sse_heartbeat_seconds: float = 15.0

    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    ollama_base_url: str = "http://localhost:11434"
    ollama_api_key: SecretStr | None = None
    llm_provider: str = "openai"
    agent_models_config_path: Path = Path("config/agents.yaml")
    specialist_registry_path: Path = Path("config/specialists.yaml")
    agent_skills_config_path: Path = Path("config/agent_skills.yaml")

    database_url: str
    checkpoint_database_url: str
    business_data_mode: Literal["embedded", "external"] = "embedded"
    query_engine: Literal["postgres"] = "postgres"
    agent_database_url: SecretStr
    agent_database_connect_timeout_seconds: int = 10
    query_engine_retry_attempts: int = 2
    query_engine_retry_base_seconds: float = 0.25
    redis_url: str

    model_validation_on_startup: bool = True
    model_validation_mode: Literal["off", "catalog", "probe"] = "probe"
    model_validation_failure_policy: Literal["warn", "fail"] = "warn"
    model_validation_timeout_seconds: float = 20.0
    model_validation_cache_ttl_seconds: int = 300

    run_lease_seconds: int = 360
    run_lease_heartbeat_seconds: int = 30
    max_concurrent_runs_per_user: int = 2
    max_concurrent_llm_calls: int = 8

    autonomous_society_enabled: bool = True
    autonomous_max_iterations: int = 4
    autonomous_max_tasks: int = 8
    autonomous_max_parallel_tasks: int = 3
    autonomous_max_queries: int = 4
    autonomous_max_llm_tokens: int = 120_000
    autonomous_max_active_execution_seconds: int = 600
    autonomous_max_total_plan_cost: float = 500_000
    autonomous_max_total_plan_rows: int = 1_000_000
    autonomous_max_total_relation_bytes: int = 2 * 1024 * 1024 * 1024
    autonomous_max_total_database_seconds: float = 90.0

    autonomous_adaptive_routing_enabled: bool = True
    autonomous_conditional_review_enabled: bool = True
    autonomous_review_high_cost_ratio: float = 0.70
    autonomous_review_high_row_ratio: float = 0.70
    semantic_context_max_documents: int = 4
    semantic_context_max_examples: int = 1
    semantic_context_max_metrics: int = 0
    semantic_context_max_dimensions: int = 0
    semantic_context_max_document_items: int = 8
    semantic_context_max_source_contracts: int = 0
    specialist_history_max_messages: int = 2
    specialist_history_max_chars: int = 1600
    specialist_prior_evidence_max_items: int = 3
    specialist_prior_evidence_max_rows: int = 2

    agent_cache_enabled: bool = True
    agent_cache_namespace: str = "axiz:agent-cache:v19"
    agent_cache_default_ttl_seconds: int = 900

    semantic_catalog_path: Path = Path("semantic_catalog")
    sql_dialect: str = "postgres"
    max_result_rows: int = 500
    max_plan_rows: int = 250_000
    max_plan_cost: float = 150_000
    max_relation_bytes: int = 512 * 1024 * 1024
    sql_timeout_seconds: int = 20
    max_sql_repair_attempts: int = 2
    max_feedback_repair_attempts: int = 2
    conversation_memory_result_sample_rows: int = 5

    excel_export_enabled: bool = True
    excel_export_max_rows: int = 5_000
    excel_export_allow_truncated: bool = False

    teams_enabled: bool = False
    teams_port: int = 3978
    teams_bot_id: str | None = None
    teams_bot_password: SecretStr | None = None
    teams_tenant_id: str | None = None
    teams_oauth_connection_name: str | None = None

    cors_origins: list[str]

    @field_validator("bootstrap_username")
    @classmethod
    def validate_bootstrap_username(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("BOOTSTRAP_USERNAME must not be empty")
        return normalized

    @field_validator("bootstrap_roles")
    @classmethod
    def validate_bootstrap_roles(cls, value: list[str]) -> list[str]:
        roles = list(dict.fromkeys(role.strip() for role in value if role.strip()))
        if not roles:
            raise ValueError("BOOTSTRAP_ROLES must contain at least one role")
        return roles

    @field_validator("database_url", "checkpoint_database_url", "redis_url")
    @classmethod
    def validate_connection_string(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Connection URLs must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_required_secrets(self) -> "Settings":
        requirements = {
            "APP_SECRET_KEY": (self.app_secret_key, 32),
            "BOOTSTRAP_PASSWORD": (self.bootstrap_password, 12),
            "INTERNAL_SERVICE_KEY": (self.internal_service_key, 24),
            "AGENT_DATABASE_URL": (self.agent_database_url, 20),
        }
        forbidden_fragments = (
            "change-me",
            "changeme",
            "replace-",
            "<required",
            "example-password",
        )
        for variable, (secret, minimum_length) in requirements.items():
            value = secret.get_secret_value().strip()
            if len(value) < minimum_length:
                raise ValueError(
                    f"{variable} must contain at least {minimum_length} characters"
                )
            lowered = value.lower()
            if any(fragment in lowered for fragment in forbidden_fragments):
                raise ValueError(f"{variable} still contains an example/placeholder value")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
