from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.services import llm as llm_module
from axiz.pe.sql_agent.services.llm import AgentModelRegistry, StructuredLLM


class StructuredAnswer(BaseModel):
    value: str


def _registry(tmp_path: Path, yaml_text: str) -> AgentModelRegistry:
    path = tmp_path / "agents.yaml"
    path.write_text(yaml_text.strip(), encoding="utf-8")
    return AgentModelRegistry(path)


@pytest.mark.asyncio
async def test_openai_adapter_translates_profile_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(
        tmp_path,
        """
default:
  provider: openai
  model: gpt-4.1
  model_context_limit_tokens: 1047576
  context_window_tokens: 1047576
  max_input_tokens: 32000
  max_output_tokens: 1800
  temperature: 0.0
  reasoning_effort: null
  verbosity: low
  service_tier: scale
  truncation: disabled
agents:
  result_verifier: {}
""",
    )
    captured: dict = {}

    class FakeResponses:
        async def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_parsed=StructuredAnswer(value="ok"))

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.responses = FakeResponses()

        async def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(llm_module, "AsyncOpenAI", FakeAsyncOpenAI)
    service = StructuredLLM(
        Settings(openai_api_key=SecretStr("test-key")),
        agent_name="result_verifier",
        registry=registry,
    )

    result = await service.parse(
        system="Return structured JSON.",
        user="Verify this result.",
        response_model=StructuredAnswer,
    )

    assert result.value == "ok"
    assert captured["model"] == "gpt-4.1"
    assert captured["max_output_tokens"] == 1800
    assert captured["temperature"] == 0.0
    assert captured["verbosity"] == "low"
    assert captured["service_tier"] == "scale"
    assert captured["truncation"] == "disabled"
    assert captured["text_format"] is StructuredAnswer
    assert captured["closed"] is True


@pytest.mark.asyncio
async def test_ollama_adapter_translates_context_sampling_and_json_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(
        tmp_path,
        """
default:
  provider: ollama
  model: qwen3:8b
  base_url: http://ollama:11434
  model_context_limit_tokens: 40960
  context_window_tokens: 32768
  max_input_tokens: 24000
  max_output_tokens: 1600
  temperature: 0.2
  top_p: 0.9
  seed: 42
  stop_sequences: ["<END>"]
  reasoning_effort: low
  ollama:
    top_k: 20
    min_p: 0.0
    repeat_penalty: 1.05
    repeat_last_n: 64
    keep_alive: 10m
    think: low
agents:
  intent_domain: {}
""",
    )
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"message": {"content": '{"value":"ok"}'}}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    service = StructuredLLM(
        Settings(),
        agent_name="intent_domain",
        registry=registry,
    )

    result = await service.parse(
        system="Return structured JSON.",
        user="Classify this question.",
        response_model=StructuredAnswer,
    )

    payload = captured["json"]
    assert result.value == "ok"
    assert captured["url"] == "http://ollama:11434/api/chat"
    assert payload["format"] == StructuredAnswer.model_json_schema()
    assert payload["stream"] is False
    assert payload["think"] == "low"
    assert payload["keep_alive"] == "10m"
    assert payload["options"] == {
        "num_ctx": 32768,
        "num_predict": 1600,
        "temperature": 0.2,
        "top_p": 0.9,
        "seed": 42,
        "stop": ["<END>"],
        "top_k": 20,
        "min_p": 0.0,
        "repeat_penalty": 1.05,
        "repeat_last_n": 64,
    }
