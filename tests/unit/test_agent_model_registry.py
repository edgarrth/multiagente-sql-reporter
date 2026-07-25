from pathlib import Path

import pytest

from axiz.pe.sql_agent.services.llm import (
    AgentModelRegistry,
    LLMConfigurationError,
    ModelProfile,
    PromptBudget,
)


def test_each_agent_can_use_a_different_provider_model_and_parameters(tmp_path: Path) -> None:
    config = tmp_path / "agents.yaml"
    config.write_text(
        """
presets:
  openai_sql:
    provider: openai
    model: gpt-5.6-terra
    model_context_limit_tokens: 1050000
    context_window_tokens: 1050000
    max_input_tokens: 64000
    max_output_tokens: 5000
    reasoning_effort: high
    temperature: null
    top_p: null
  ollama_router:
    provider: ollama
    model: qwen3:8b
    model_context_limit_tokens: 40960
    context_window_tokens: 32768
    max_input_tokens: 24000
    max_output_tokens: 1200
    reasoning_effort: low
    temperature: 0.2
    top_p: 0.9
    seed: 42
    ollama:
      top_k: 20
      repeat_penalty: 1.05
default:
  preset: openai_sql
agents:
  intent_domain:
    preset: ollama_router
  sql_generator:
    preset: openai_sql
  result_verifier:
    provider: openai
    model: gpt-4.1
    model_context_limit_tokens: 1047576
    context_window_tokens: 1047576
    max_input_tokens: 32000
    max_output_tokens: 1800
    reasoning_effort: null
    temperature: 0.0
""".strip(),
        encoding="utf-8",
    )

    registry = AgentModelRegistry(config)

    sql = registry.profile_for("sql_generator")
    assert sql.provider == "openai"
    assert sql.model == "gpt-5.6-terra"
    assert sql.reasoning_effort == "high"
    assert sql.temperature is None

    router = registry.profile_for("intent_domain")
    assert router.provider == "ollama"
    assert router.model == "qwen3:8b"
    assert router.temperature == 0.2
    assert router.context_window_tokens == 32768
    assert router.ollama.top_k == 20

    verifier = registry.profile_for("result_verifier")
    assert verifier.model == "gpt-4.1"
    assert verifier.temperature == 0.0
    assert verifier.reasoning_effort is None


def test_model_configuration_supports_environment_preset_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AXIZ_TEST_PRESET", "ollama_local")
    config = tmp_path / "agents.yaml"
    config.write_text(
        """
presets:
  openai_default:
    provider: openai
    model: gpt-5.6-luna
    service_tier: auto
  ollama_local:
    provider: ollama
    model: qwen3:8b
    model_context_limit_tokens: 40960
    context_window_tokens: 32768
    max_input_tokens: 24000
    max_output_tokens: 1200
    temperature: 0.2
default:
  preset: openai_default
agents:
  intent_domain:
    preset: ${AXIZ_TEST_PRESET:-openai_default}
""".strip(),
        encoding="utf-8",
    )

    registry = AgentModelRegistry(config)
    profile = registry.profile_for("intent_domain")
    assert profile.provider == "ollama"
    assert profile.model == "qwen3:8b"
    assert profile.service_tier is None


def test_context_budget_is_validated() -> None:
    with pytest.raises(ValueError):
        ModelProfile(
            provider="ollama",
            model="qwen3:8b",
            model_context_limit_tokens=40960,
            context_window_tokens=32768,
            max_input_tokens=30000,
            max_output_tokens=5000,
        )


def test_openai_reasoning_profile_rejects_sampling_controls() -> None:
    with pytest.raises(ValueError):
        ModelProfile(
            provider="openai",
            model="gpt-5.6-terra",
            reasoning_effort="high",
            temperature=0.2,
        )


def test_prompt_budget_can_truncate_only_when_explicitly_enabled() -> None:
    profile = ModelProfile(
        provider="ollama",
        model="qwen3:8b",
        model_context_limit_tokens=40960,
        context_window_tokens=40960,
        max_input_tokens=30,
        max_output_tokens=100,
        input_overflow_strategy="truncate_user_tail",
    )
    system, user = PromptBudget.fit(profile, "system", "x" * 1000)
    assert system == "system"
    assert "INPUT TRUNCATED" in user


def test_unknown_preset_fails_fast(tmp_path: Path) -> None:
    config = tmp_path / "agents.yaml"
    config.write_text(
        """
default:
  preset: missing
agents: {}
""".strip(),
        encoding="utf-8",
    )
    registry = AgentModelRegistry(config)
    with pytest.raises(LLMConfigurationError):
        registry.profile_for("any")


def test_openai_profile_rejects_provider_unsupported_options() -> None:
    with pytest.raises(ValueError):
        ModelProfile(provider="openai", model="gpt-4.1", seed=42)
    with pytest.raises(ValueError):
        ModelProfile(provider="openai", model="gpt-4.1", stop_sequences=["END"])


def test_ollama_profile_rejects_openai_only_options() -> None:
    with pytest.raises(ValueError):
        ModelProfile(provider="ollama", model="qwen3:8b", service_tier="auto")
    with pytest.raises(ValueError):
        ModelProfile(provider="ollama", model="qwen3:8b", verbosity="low")
