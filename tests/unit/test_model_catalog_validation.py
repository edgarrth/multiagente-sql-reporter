from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.services.llm import AgentModelRegistry
from axiz.pe.sql_agent.services.model_validation import ModelCatalogValidator


class FakeModels:
    def __init__(self, catalog_error: Exception | None = None) -> None:
        self.catalog_error = catalog_error

    async def retrieve(self, model: str):
        if self.catalog_error:
            raise self.catalog_error
        return SimpleNamespace(id=model)


class FakeResponses:
    async def parse(self, **kwargs):
        return SimpleNamespace(output_parsed=SimpleNamespace(ok=True))


class FakeOpenAI:
    catalog_error: Exception | None = None

    def __init__(self, **kwargs) -> None:
        self.models = FakeModels(self.catalog_error)
        self.responses = FakeResponses()

    async def close(self) -> None:
        return None


def _registry(tmp_path: Path) -> AgentModelRegistry:
    path = tmp_path / "agents.yaml"
    path.write_text(
        """
presets:
  private_alias:
    provider: openai
    model: private-model-alias
    base_url: https://api.example.test/v1
    model_context_limit_tokens: 128000
    context_window_tokens: 16000
    max_input_tokens: 12000
    max_output_tokens: 1000
    reasoning_effort: low
    temperature: null
    top_p: null
    timeout_seconds: 10
    max_retries: 0
    store: false
    truncation: disabled
default:
  preset: private_alias
agents:
  intent_domain:
    preset: private_alias
  sql_generator:
    preset: private_alias
""".strip(),
        encoding="utf-8",
    )
    return AgentModelRegistry(path)


@pytest.mark.asyncio
async def test_probe_validates_catalog_and_structured_output(monkeypatch, tmp_path: Path) -> None:
    from axiz.pe.sql_agent.services import model_validation

    FakeOpenAI.catalog_error = None
    monkeypatch.setattr(model_validation, "AsyncOpenAI", FakeOpenAI)
    settings = Settings(
        openai_api_key=SecretStr("test-key"),
        model_validation_mode="probe",
        model_validation_failure_policy="fail",
    )
    report = await ModelCatalogValidator(settings, _registry(tmp_path)).validate(force=True)

    assert report.ready is True
    assert report.unique_model_count == 1
    assert report.valid_count == 1
    assert report.items[0].catalog_available is True
    assert report.items[0].structured_output_supported is True


@pytest.mark.asyncio
async def test_private_alias_probe_can_override_missing_catalog_entry(
    monkeypatch, tmp_path: Path
) -> None:
    from axiz.pe.sql_agent.services import model_validation

    FakeOpenAI.catalog_error = RuntimeError("not listed")
    monkeypatch.setattr(model_validation, "AsyncOpenAI", FakeOpenAI)
    settings = Settings(
        openai_api_key=SecretStr("test-key"),
        model_validation_mode="probe",
        model_validation_failure_policy="fail",
    )
    report = await ModelCatalogValidator(settings, _registry(tmp_path)).validate(force=True)

    assert report.ready is True
    assert report.warning_count == 1
    assert report.items[0].status == "warning"
    assert report.items[0].structured_output_supported is True
    assert "alias" in " ".join(report.items[0].warnings).lower()
