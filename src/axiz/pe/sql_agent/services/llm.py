from __future__ import annotations

import asyncio
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Literal, TypeVar

import httpx
import yaml

try:
    from openai import AsyncOpenAI
except ImportError:  # Allows config inspection before optional runtime deps are installed.
    AsyncOpenAI = None  # type: ignore[assignment,misc]

from pydantic import BaseModel, Field, model_validator

from axiz.pe.sql_agent.config import Settings

T = TypeVar("T", bound=BaseModel)
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?}")


class LLMConfigurationError(RuntimeError):
    pass


class LLMContextLimitError(RuntimeError):
    pass


class OllamaOptions(BaseModel):
    """Native Ollama generation options sent to /api/chat."""

    top_k: int | None = Field(default=None, ge=0)
    min_p: float | None = Field(default=None, ge=0, le=1)
    repeat_penalty: float | None = Field(default=None, gt=0)
    repeat_last_n: int | None = Field(default=None, ge=-1)
    keep_alive: str | int | None = None
    think: bool | Literal["low", "medium", "high", "max"] | None = None


class ModelProfile(BaseModel):
    """Effective provider/model profile for one specialist agent.

    `model_context_limit_tokens` is metadata for the real model limit.
    `context_window_tokens` is the application allocation. OpenAI models have a fixed
    provider-side window; for Ollama this value is also sent as `num_ctx`.
    """

    provider: Literal["openai", "ollama"] = "openai"
    model: str
    base_url: str | None = None
    api_key_env: str | None = None

    model_context_limit_tokens: int = Field(default=128000, ge=1024)
    context_window_tokens: int = Field(default=128000, ge=1024)
    max_input_tokens: int = Field(default=16000, ge=1)
    max_output_tokens: int = Field(default=4096, ge=1)
    input_overflow_strategy: Literal["error", "truncate_user_tail"] = "error"

    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    seed: int | None = None
    stop_sequences: list[str] = Field(default_factory=list)

    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = None
    reasoning_mode: Literal["standard", "pro"] = "standard"
    verbosity: Literal["low", "medium", "high"] | None = None

    timeout_seconds: float = Field(default=90, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)
    store: bool = False
    service_tier: Literal["auto", "default", "flex", "scale", "priority"] | None = None
    truncation: Literal["auto", "disabled"] = "disabled"
    ollama: OllamaOptions = Field(default_factory=OllamaOptions)

    @model_validator(mode="after")
    def validate_profile(self) -> "ModelProfile":
        if self.context_window_tokens > self.model_context_limit_tokens:
            raise ValueError(
                "context_window_tokens cannot exceed model_context_limit_tokens"
            )
        if self.max_input_tokens + self.max_output_tokens > self.context_window_tokens:
            raise ValueError(
                "max_input_tokens + max_output_tokens must fit inside context_window_tokens"
            )
        if self.provider == "openai" and self.temperature is not None and self.top_p is not None:
            raise ValueError(
                "For OpenAI configure temperature or top_p, not both; leave one as null"
            )
        if (
            self.provider == "openai"
            and self.reasoning_effort not in (None, "none")
            and (self.temperature is not None or self.top_p is not None)
        ):
            raise ValueError(
                "OpenAI reasoning profiles should leave temperature/top_p null. "
                "Set reasoning_effort to none before enabling sampling controls."
            )
        if self.provider == "openai" and self.seed is not None:
            raise ValueError("seed is not supported by the OpenAI Responses API")
        if self.provider == "openai" and self.stop_sequences:
            raise ValueError("stop_sequences are not supported by the OpenAI Responses API")
        if self.provider == "ollama" and self.reasoning_mode == "pro":
            raise ValueError("reasoning_mode=pro is specific to OpenAI GPT-5.6")
        if self.provider == "ollama" and self.service_tier is not None:
            raise ValueError("service_tier is specific to OpenAI")
        if self.provider == "ollama" and self.verbosity is not None:
            raise ValueError("verbosity is specific to OpenAI Responses")
        return self


class AgentModelsDocument(BaseModel):
    presets: dict[str, dict[str, Any]] = Field(default_factory=dict)
    default: dict[str, Any]
    agents: dict[str, dict[str, Any]] = Field(default_factory=dict)


class AgentModelRegistry:
    """Loads model presets and per-agent overrides without coupling LangGraph to model IDs."""

    def __init__(self, path: Path, default_provider: str = "openai") -> None:
        self.path = path
        self.default_provider = default_provider
        self.document = self._load()

    @staticmethod
    def _expand_env(value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            name, fallback = match.group(1), match.group(2)
            return os.getenv(name, fallback or "")

        return _ENV_PATTERN.sub(replace, value)

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if (
                key in merged
                and isinstance(merged[key], dict)
                and isinstance(value, dict)
            ):
                merged[key] = AgentModelRegistry._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _load(self) -> AgentModelsDocument:
        if not self.path.exists():
            raise LLMConfigurationError(f"Agent model configuration not found: {self.path}")
        raw_text = self._expand_env(self.path.read_text(encoding="utf-8"))
        payload = yaml.safe_load(raw_text) or {}
        payload.setdefault("default", {})
        payload.setdefault("presets", {})
        payload.setdefault("agents", {})
        return AgentModelsDocument.model_validate(payload)

    def reload(self) -> None:
        self.document = self._load()

    def _resolve_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        entry = dict(entry)
        preset_name = entry.pop("preset", None)
        resolved: dict[str, Any] = {}
        if preset_name:
            if preset_name not in self.document.presets:
                raise LLMConfigurationError(f"Unknown model preset: {preset_name}")
            resolved = self._deep_merge(resolved, self.document.presets[preset_name])
        return self._deep_merge(resolved, entry)

    def profile_for(self, agent_name: str) -> ModelProfile:
        default_profile = self._resolve_entry(self.document.default)
        agent_entry = self.document.agents.get(agent_name, {})

        # A per-agent preset is a complete provider/model profile. It replaces the
        # default preset so OpenAI-only fields cannot leak into an Ollama profile
        # (or vice versa). Agent entries without a preset act as ordinary overrides.
        if agent_entry and agent_entry.get("preset"):
            merged = self._resolve_entry(agent_entry)
        else:
            merged = self._deep_merge(default_profile, self._resolve_entry(agent_entry))

        merged.setdefault("provider", self.default_provider)
        try:
            return ModelProfile.model_validate(merged)
        except Exception as exc:
            raise LLMConfigurationError(
                f"Invalid model profile for agent {agent_name!r}: {exc}"
            ) from exc

    def list_profiles(self) -> dict[str, dict[str, Any]]:
        return {
            name: self.profile_for(name).model_dump()
            for name in sorted(self.document.agents)
        }

    def list_presets(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name in sorted(self.document.presets):
            try:
                result[name] = ModelProfile.model_validate(
                    self.document.presets[name]
                ).model_dump()
            except Exception as exc:
                result[name] = {"invalid": True, "error": str(exc)}
        return result


class PromptBudget:
    """Conservative provider-agnostic prompt budgeting.

    Tokenization differs by provider and model. The estimator intentionally assumes roughly
    3.5 UTF-8 characters per token, which is conservative for mixed Spanish/English JSON prompts.
    """

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, math.ceil(len(text) / 3.5))

    @classmethod
    def fit(cls, profile: ModelProfile, system: str, user: str) -> tuple[str, str]:
        system_tokens = cls.estimate_tokens(system)
        user_tokens = cls.estimate_tokens(user)
        total = system_tokens + user_tokens
        if total <= profile.max_input_tokens:
            return system, user

        if profile.input_overflow_strategy == "error":
            raise LLMContextLimitError(
                f"Estimated input ({total} tokens) exceeds max_input_tokens="
                f"{profile.max_input_tokens} for {profile.provider}/{profile.model}"
            )

        available_user_tokens = profile.max_input_tokens - system_tokens
        if available_user_tokens <= 0:
            raise LLMContextLimitError(
                "The system prompt alone exceeds the configured max_input_tokens"
            )
        max_chars = max(1, int(available_user_tokens * 3.5))
        marker = "\n[INPUT TRUNCATED BY CONFIGURED CONTEXT BUDGET]"
        return system, user[: max(1, max_chars - len(marker))] + marker


class StructuredLLM:
    """Provider-neutral structured-output adapter for OpenAI and native Ollama."""

    def __init__(
        self,
        settings: Settings,
        *,
        agent_name: str,
        registry: AgentModelRegistry,
    ) -> None:
        self.settings = settings
        self.agent_name = agent_name
        self.registry = registry

    def _api_key(self, profile: ModelProfile) -> str | None:
        if profile.api_key_env:
            return os.getenv(profile.api_key_env)
        if profile.provider == "openai":
            return (
                self.settings.openai_api_key.get_secret_value()
                if self.settings.openai_api_key
                else None
            )
        return (
            self.settings.ollama_api_key.get_secret_value()
            if self.settings.ollama_api_key
            else None
        )

    async def parse(
        self,
        *,
        system: str,
        user: str,
        response_model: type[T],
    ) -> T:
        profile = self.registry.profile_for(self.agent_name)
        system, user = PromptBudget.fit(profile, system, user)
        if profile.provider == "openai":
            return await self._parse_openai(profile, system, user, response_model)
        if profile.provider == "ollama":
            return await self._parse_ollama(profile, system, user, response_model)
        raise LLMConfigurationError(
            f"Unsupported provider={profile.provider!r} for agent {self.agent_name!r}"
        )

    async def _parse_openai(
        self,
        profile: ModelProfile,
        system: str,
        user: str,
        response_model: type[T],
    ) -> T:
        if AsyncOpenAI is None:
            raise LLMConfigurationError("The openai package is required for provider=openai")
        api_key = self._api_key(profile)
        if not api_key:
            raise LLMConfigurationError("OPENAI_API_KEY is required for provider=openai")

        client = AsyncOpenAI(
            api_key=api_key,
            base_url=profile.base_url or self.settings.openai_base_url,
            timeout=profile.timeout_seconds,
            max_retries=profile.max_retries,
        )
        request: dict[str, Any] = {
            "model": profile.model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "text_format": response_model,
            "max_output_tokens": profile.max_output_tokens,
            "store": profile.store,
        }
        if profile.reasoning_effort is not None:
            reasoning: dict[str, Any] = {"effort": profile.reasoning_effort}
            if profile.reasoning_mode == "pro":
                reasoning["mode"] = "pro"
            request["reasoning"] = reasoning
        if profile.verbosity is not None:
            request["verbosity"] = profile.verbosity
        if profile.temperature is not None:
            request["temperature"] = profile.temperature
        if profile.top_p is not None:
            request["top_p"] = profile.top_p
        if profile.service_tier is not None:
            request["service_tier"] = profile.service_tier
        request["truncation"] = profile.truncation

        try:
            response = await client.responses.parse(**request)
        finally:
            await client.close()

        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError(
                f"Agent {self.agent_name!r} using {profile.model!r} returned no structured output"
            )
        return parsed

    async def _parse_ollama(
        self,
        profile: ModelProfile,
        system: str,
        user: str,
        response_model: type[T],
    ) -> T:
        base_url = (profile.base_url or self.settings.ollama_base_url).rstrip("/")
        headers = {"Content-Type": "application/json"}
        api_key = self._api_key(profile)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        options: dict[str, Any] = {
            "num_ctx": profile.context_window_tokens,
            "num_predict": profile.max_output_tokens,
        }
        optional_options = {
            "temperature": profile.temperature,
            "top_p": profile.top_p,
            "seed": profile.seed,
            "stop": profile.stop_sequences or None,
            "top_k": profile.ollama.top_k,
            "min_p": profile.ollama.min_p,
            "repeat_penalty": profile.ollama.repeat_penalty,
            "repeat_last_n": profile.ollama.repeat_last_n,
        }
        options.update({key: value for key, value in optional_options.items() if value is not None})

        think: bool | str | None = profile.ollama.think
        if think is None and profile.reasoning_effort not in (None, "none"):
            think = profile.reasoning_effort

        payload: dict[str, Any] = {
            "model": profile.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": response_model.model_json_schema(),
            "stream": False,
            "options": options,
        }
        if think is not None:
            payload["think"] = think
        if profile.ollama.keep_alive is not None:
            payload["keep_alive"] = profile.ollama.keep_alive

        response_payload: dict[str, Any] | None = None
        last_error: Exception | None = None
        for attempt in range(profile.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=profile.timeout_seconds) as client:
                    response = await client.post(
                        f"{base_url}/api/chat",
                        headers=headers,
                        json=payload,
                    )
                if response.status_code >= 500 and attempt < profile.max_retries:
                    await asyncio.sleep(min(2**attempt, 4))
                    continue
                response.raise_for_status()
                response_payload = response.json()
                break
            except httpx.HTTPStatusError as exc:
                last_error = exc
                # Client errors are configuration/prompt errors and should not be retried.
                if 400 <= exc.response.status_code < 500:
                    raise RuntimeError(
                        f"Ollama rejected the request for agent {self.agent_name!r}: {exc}"
                    ) from exc
                if attempt >= profile.max_retries:
                    raise RuntimeError(
                        f"Ollama request failed for agent {self.agent_name!r}: {exc}"
                    ) from exc
                await asyncio.sleep(min(2**attempt, 4))
            except (httpx.RequestError, ValueError) as exc:
                last_error = exc
                if attempt >= profile.max_retries:
                    raise RuntimeError(
                        f"Ollama request failed for agent {self.agent_name!r}: {exc}"
                    ) from exc
                await asyncio.sleep(min(2**attempt, 4))

        if response_payload is None:
            raise RuntimeError(
                f"Ollama returned no response for agent {self.agent_name!r}: {last_error}"
            )
        content = response_payload.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(
                f"Ollama model {profile.model!r} returned no structured message content"
            )
        try:
            return response_model.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(
                f"Ollama model {profile.model!r} returned invalid structured output: {exc}"
            ) from exc


class StructuredLLMFactory:
    def __init__(self, settings: Settings, registry: AgentModelRegistry) -> None:
        self.settings = settings
        self.registry = registry

    def for_agent(self, agent_name: str) -> StructuredLLM:
        return StructuredLLM(
            self.settings,
            agent_name=agent_name,
            registry=self.registry,
        )
