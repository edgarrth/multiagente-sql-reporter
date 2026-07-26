from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.services import llm as llm_module
from axiz.pe.sql_agent.services.llm import AgentModelRegistry, StructuredLLM
from axiz.pe.sql_agent.services.llm_usage import (
    activate_llm_usage_collection,
    reset_llm_usage_collection,
)


class StructuredAnswer(BaseModel):
    value: str


def _registry(tmp_path: Path, yaml_text: str) -> AgentModelRegistry:
    path = tmp_path / "agents.yaml"
    path.write_text(yaml_text.strip(), encoding="utf-8")
    return AgentModelRegistry(path)


@pytest.mark.asyncio
async def test_openai_usage_is_collected_with_cache_and_reasoning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(
        tmp_path,
        """
default:
  provider: openai
  model: gpt-4.1
  model_context_limit_tokens: 128000
  context_window_tokens: 128000
  max_input_tokens: 16000
  max_output_tokens: 1000
  reasoning_effort: null
agents:
  result_verifier: {}
""",
    )

    class FakeResponses:
        async def parse(self, **kwargs):
            return SimpleNamespace(
                output_parsed=StructuredAnswer(value="ok"),
                usage=SimpleNamespace(
                    input_tokens=120,
                    output_tokens=45,
                    total_tokens=165,
                    input_tokens_details=SimpleNamespace(cached_tokens=40),
                    output_tokens_details=SimpleNamespace(reasoning_tokens=15),
                ),
            )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.responses = FakeResponses()

        async def close(self) -> None:
            return None

    monkeypatch.setattr(llm_module, "AsyncOpenAI", FakeAsyncOpenAI)
    collector, token = activate_llm_usage_collection()
    try:
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
    finally:
        reset_llm_usage_collection(token)

    summary = collector.summary()
    assert result.value == "ok"
    assert summary.call_count == 1
    assert summary.actual_input_tokens == 120
    assert summary.actual_output_tokens == 45
    assert summary.actual_total_tokens == 165
    assert summary.cached_input_tokens == 40
    assert summary.reasoning_output_tokens == 15
    assert summary.calls[0].agent == "result_verifier"
    assert summary.calls[0].reserved_output_tokens == 1000


@pytest.mark.asyncio
async def test_ollama_usage_is_collected_from_native_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry(
        tmp_path,
        """
default:
  provider: ollama
  model: qwen3:8b
  base_url: http://host.docker.internal:11434
  model_context_limit_tokens: 40960
  context_window_tokens: 32768
  max_input_tokens: 24000
  max_output_tokens: 1200
  reasoning_effort: low
agents:
  intent_domain: {}
""",
    )

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "message": {"content": '{"value":"ok"}'},
                "prompt_eval_count": 90,
                "eval_count": 30,
                "total_duration": 125_000_000,
            }

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    collector, token = activate_llm_usage_collection()
    try:
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
    finally:
        reset_llm_usage_collection(token)

    summary = collector.summary()
    assert result.value == "ok"
    assert summary.actual_input_tokens == 90
    assert summary.actual_output_tokens == 30
    assert summary.actual_total_tokens == 120
    assert summary.calls[0].duration_ms == 125.0


def test_usage_collector_can_continue_a_hitl_run() -> None:
    first, first_token = activate_llm_usage_collection()
    try:
        from axiz.pe.sql_agent.models.contracts import LLMCallUsage

        first.record(
            LLMCallUsage(
                call_id="1",
                agent="intent_domain",
                provider="openai",
                model="gpt-4.1",
                estimated_input_tokens=100,
                reserved_output_tokens=50,
                estimated_max_total_tokens=150,
                input_tokens=80,
                output_tokens=20,
                total_tokens=100,
            )
        )
    finally:
        reset_llm_usage_collection(first_token)

    resumed, resumed_token = activate_llm_usage_collection(
        first.summary().model_dump(mode="json")
    )
    try:
        resumed.record(
            LLMCallUsage(
                call_id="2",
                agent="sql_generator",
                provider="ollama",
                model="qwen3-coder:30b",
                estimated_input_tokens=200,
                reserved_output_tokens=100,
                estimated_max_total_tokens=300,
                input_tokens=160,
                output_tokens=40,
                total_tokens=200,
            )
        )
    finally:
        reset_llm_usage_collection(resumed_token)

    summary = resumed.summary()
    assert summary.call_count == 2
    assert summary.actual_total_tokens == 300
    assert summary.estimated_max_total_tokens == 450
