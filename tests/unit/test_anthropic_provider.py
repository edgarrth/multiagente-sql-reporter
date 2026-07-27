from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.services import llm as llm_module
from axiz.pe.sql_agent.services.llm import (
    AgentModelRegistry,
    ModelProfile,
    StructuredLLM,
)


ROOT = Path(__file__).resolve().parents[2]


class ExampleOutput(BaseModel):
    answer: str


def test_repository_exposes_valid_anthropic_presets() -> None:
    registry = AgentModelRegistry(ROOT / "config/agents.yaml")
    presets = registry.list_presets()

    expected = {
        "anthropic_claude_opus_5_quality",
        "anthropic_claude_sonnet_5_balanced",
        "anthropic_claude_sonnet_5_sql",
        "anthropic_claude_sonnet_5_explanation",
        "anthropic_claude_haiku_4_5_routing",
    }
    assert expected <= presets.keys()
    for name in expected:
        assert presets[name].get("invalid") is not True
        assert presets[name]["provider"] == "anthropic"
        assert presets[name]["temperature"] is None
        assert presets[name]["top_p"] is None
        assert presets[name]["anthropic"]["top_k"] is None


def test_current_anthropic_profile_rejects_sampling_controls() -> None:
    with pytest.raises(ValueError, match="sampling_mode=omit"):
        ModelProfile(
            provider="anthropic",
            model="claude-sonnet-5",
            model_context_limit_tokens=1_000_000,
            context_window_tokens=1_000_000,
            max_input_tokens=32_000,
            max_output_tokens=3_000,
            reasoning_effort="medium",
            temperature=0.2,
            anthropic={"thinking": "adaptive", "sampling_mode": "omit"},
        )


@pytest.mark.asyncio
async def test_anthropic_messages_request_uses_json_schema_and_omits_sampling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "agents.yaml"
    config.write_text(
        """
presets:
  claude_sql:
    provider: anthropic
    model: claude-sonnet-5
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
    model_context_limit_tokens: 1000000
    context_window_tokens: 1000000
    max_input_tokens: 32000
    max_output_tokens: 3000
    reasoning_effort: medium
    temperature: null
    top_p: null
    anthropic:
      thinking: adaptive
      sampling_mode: omit
      top_k: null
default:
  preset: claude_sql
agents:
  sql_generator:
    preset: claude_sql
""".strip(),
        encoding="utf-8",
    )
    captured: dict = {}

    class FakeMessages:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text='{"answer":"ok"}')],
                stop_reason="end_turn",
                usage=SimpleNamespace(
                    input_tokens=25,
                    output_tokens=8,
                    cache_read_input_tokens=0,
                ),
            )

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.messages = FakeMessages()

        async def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(llm_module, "AsyncAnthropic", FakeAnthropic)
    registry = AgentModelRegistry(config)
    adapter = StructuredLLM(
        Settings(model_validation_on_startup=False),
        agent_name="sql_generator",
        registry=registry,
    )

    result = await adapter.parse(
        system="system contract",
        user="question",
        response_model=ExampleOutput,
    )

    assert result.answer == "ok"
    assert captured["model"] == "claude-sonnet-5"
    assert captured["system"] == "system contract"
    assert captured["messages"] == [{"role": "user", "content": "question"}]
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["output_config"]["format"]["schema"]["title"] == "ExampleOutput"
    assert captured["output_config"]["effort"] == "medium"
    assert captured["thinking"] == {"type": "adaptive"}
    assert "temperature" not in captured
    assert "top_p" not in captured
    assert "top_k" not in captured
    assert captured["closed"] is True
