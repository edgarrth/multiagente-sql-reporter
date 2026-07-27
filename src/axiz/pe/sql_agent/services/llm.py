from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from uuid import uuid4
from pathlib import Path
from typing import Any, Literal, TypeVar

import httpx
import structlog
import yaml

try:
    from openai import AsyncOpenAI
except ImportError:  # Allows config inspection before optional runtime deps are installed.
    AsyncOpenAI = None  # type: ignore[assignment,misc]

try:
    from anthropic import AsyncAnthropic
except ImportError:  # Allows config inspection before optional runtime deps are installed.
    AsyncAnthropic = None  # type: ignore[assignment,misc]

from pydantic import BaseModel, Field, model_validator

from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.models.contracts import LLMCallUsage
from axiz.pe.sql_agent.services.llm_usage import (
    current_llm_scope_token_limit,
    current_llm_usage_collector,
    current_llm_usage_scope,
)

T = TypeVar("T", bound=BaseModel)
logger = structlog.get_logger(__name__)
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


class AnthropicOptions(BaseModel):
    """Anthropic Messages API options used only by Claude profiles.

    Current Claude 4.7+ and Claude 5 models reject sampling controls such as
    temperature, top_p and top_k. ``sampling_mode=omit`` is therefore the safe default.
    ``legacy`` exists only for explicitly configured older models.
    """

    thinking: Literal["adaptive", "disabled"] | None = None
    sampling_mode: Literal["omit", "legacy"] = "omit"
    top_k: int | None = Field(default=None, ge=0)


class ModelProfile(BaseModel):
    """Effective provider/model profile for one specialist agent.

    `model_context_limit_tokens` is metadata for the real model limit.
    `context_window_tokens` is the application allocation. OpenAI models have a fixed
    provider-side window; for Ollama this value is also sent as `num_ctx`.
    """

    provider: Literal["openai", "anthropic", "ollama"] = "openai"
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
    anthropic: AnthropicOptions = Field(default_factory=AnthropicOptions)
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
        if self.provider == "anthropic":
            if self.seed is not None:
                raise ValueError("seed is not supported by the Anthropic Messages API")
            if self.reasoning_mode == "pro":
                raise ValueError("reasoning_mode=pro is specific to OpenAI GPT-5.6")
            if self.service_tier is not None:
                raise ValueError("service_tier is specific to OpenAI")
            if self.verbosity is not None:
                raise ValueError("verbosity is specific to OpenAI Responses")
            if self.anthropic.sampling_mode == "omit" and any(
                value is not None
                for value in (self.temperature, self.top_p, self.anthropic.top_k)
            ):
                raise ValueError(
                    "Anthropic sampling_mode=omit requires temperature, top_p and top_k "
                    "to be null. This is required for Claude 4.7+ and Claude 5 models."
                )
            if self.anthropic.sampling_mode == "legacy":
                configured = sum(
                    value is not None
                    for value in (self.temperature, self.top_p, self.anthropic.top_k)
                )
                if configured > 1:
                    raise ValueError(
                        "For legacy Anthropic sampling configure only one of "
                        "temperature, top_p or top_k"
                    )
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
    """Provider-neutral structured-output adapter for OpenAI, Anthropic and Ollama."""

    def __init__(
        self,
        settings: Settings,
        *,
        agent_name: str,
        registry: AgentModelRegistry,
        limiter: asyncio.Semaphore | None = None,
    ) -> None:
        self.settings = settings
        self.agent_name = agent_name
        self.registry = registry
        self.limiter = limiter

    def _api_key(self, profile: ModelProfile) -> str | None:
        if profile.api_key_env:
            return os.getenv(profile.api_key_env)
        if profile.provider == "openai":
            return (
                self.settings.openai_api_key.get_secret_value()
                if self.settings.openai_api_key
                else None
            )
        if profile.provider == "anthropic":
            return (
                self.settings.anthropic_api_key.get_secret_value()
                if self.settings.anthropic_api_key
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
        estimated_input_tokens = (
            PromptBudget.estimate_tokens(system) + PromptBudget.estimate_tokens(user)
        )
        call_id = str(uuid4())
        started = time.perf_counter()
        collector = current_llm_usage_collector()
        scope_id, specialist_id = current_llm_usage_scope()
        if collector is not None:
            await collector.reserve(
                call_id,
                estimated_input_tokens + profile.max_output_tokens,
                agent=self.agent_name,
                scope_id=scope_id,
                scope_limit_tokens=current_llm_scope_token_limit(),
            )
        if self.settings.log_llm_calls:
            logger.info(
                "llm_call_started",
                call_id=call_id,
                agent=self.agent_name,
                scope_id=scope_id,
                specialist_id=specialist_id,
                provider=profile.provider,
                model=profile.model,
                estimated_input_tokens=estimated_input_tokens,
                reserved_output_tokens=profile.max_output_tokens,
                estimated_max_total_tokens=estimated_input_tokens + profile.max_output_tokens,
                response_model=response_model.__name__,
            )
        try:
            async def invoke_provider() -> tuple[T, dict[str, Any]]:
                if profile.provider == "openai":
                    return await self._parse_openai(profile, system, user, response_model)
                if profile.provider == "anthropic":
                    return await self._parse_anthropic(profile, system, user, response_model)
                if profile.provider == "ollama":
                    return await self._parse_ollama(profile, system, user, response_model)
                raise LLMConfigurationError(
                    f"Unsupported provider={profile.provider!r} "
                    f"for agent {self.agent_name!r}"
                )

            if self.limiter is None:
                parsed, actual = await invoke_provider()
            else:
                async with self.limiter:
                    parsed, actual = await invoke_provider()
        except asyncio.CancelledError as exc:
            if self.settings.log_llm_calls:
                logger.warning(
                    "llm_call_cancelled",
                    call_id=call_id,
                    agent=self.agent_name,
                    provider=profile.provider,
                    model=profile.model,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )
            if collector is not None:
                await collector.settle(
                    LLMCallUsage(
                        call_id=call_id,
                        agent=self.agent_name,
                        scope_id=scope_id,
                        specialist_id=specialist_id,
                        provider=profile.provider,
                        model=profile.model,
                        status="failed",
                        estimated_input_tokens=estimated_input_tokens,
                        reserved_output_tokens=profile.max_output_tokens,
                        estimated_max_total_tokens=(
                            estimated_input_tokens + profile.max_output_tokens
                        ),
                        duration_ms=(time.perf_counter() - started) * 1000,
                        error="cancelled",
                    )
                )
            raise
        except Exception as exc:
            if self.settings.log_llm_calls:
                logger.error(
                    "llm_call_failed",
                    call_id=call_id,
                    agent=self.agent_name,
                    scope_id=scope_id,
                    specialist_id=specialist_id,
                    provider=profile.provider,
                    model=profile.model,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                    error_type=type(exc).__name__,
                    error=str(exc),
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
            if collector is not None:
                await collector.settle(
                    LLMCallUsage(
                        call_id=call_id,
                        agent=self.agent_name,
                        scope_id=scope_id,
                        specialist_id=specialist_id,
                        provider=profile.provider,
                        model=profile.model,
                        status="failed",
                        estimated_input_tokens=estimated_input_tokens,
                        reserved_output_tokens=profile.max_output_tokens,
                        estimated_max_total_tokens=(
                            estimated_input_tokens + profile.max_output_tokens
                        ),
                        duration_ms=(time.perf_counter() - started) * 1000,
                        error=str(exc),
                    )
                )
            raise

        if self.settings.log_llm_calls:
            logger.info(
                "llm_call_completed",
                call_id=call_id,
                agent=self.agent_name,
                scope_id=scope_id,
                specialist_id=specialist_id,
                provider=profile.provider,
                model=profile.model,
                input_tokens=actual.get("input_tokens"),
                output_tokens=actual.get("output_tokens"),
                total_tokens=actual.get("total_tokens"),
                cached_input_tokens=actual.get("cached_input_tokens", 0),
                reasoning_output_tokens=actual.get("reasoning_output_tokens", 0),
                attempt_count=actual.get("attempt_count", 1),
                duration_ms=round(
                    float(actual.get("duration_ms") or (time.perf_counter() - started) * 1000),
                    2,
                ),
            )
        if collector is not None:
            await collector.settle(
                LLMCallUsage(
                    call_id=call_id,
                    agent=self.agent_name,
                    scope_id=scope_id,
                    specialist_id=specialist_id,
                    provider=profile.provider,
                    model=profile.model,
                    status="completed",
                    estimated_input_tokens=estimated_input_tokens,
                    reserved_output_tokens=profile.max_output_tokens,
                    estimated_max_total_tokens=(
                        estimated_input_tokens + profile.max_output_tokens
                    ),
                    input_tokens=actual.get("input_tokens"),
                    output_tokens=actual.get("output_tokens"),
                    total_tokens=actual.get("total_tokens"),
                    cached_input_tokens=actual.get("cached_input_tokens", 0),
                    reasoning_output_tokens=actual.get("reasoning_output_tokens", 0),
                    duration_ms=actual.get("duration_ms")
                    or (time.perf_counter() - started) * 1000,
                    attempt_count=actual.get("attempt_count", 1),
                )
            )
        return parsed

    async def _parse_openai(
        self,
        profile: ModelProfile,
        system: str,
        user: str,
        response_model: type[T],
    ) -> tuple[T, dict[str, Any]]:
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
            # Responses API expects verbosity under text.verbosity. The SDK's
            # responses.parse helper merges this with text_format for Structured Outputs.
            request["text"] = {"verbosity": profile.verbosity}
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
        usage = getattr(response, "usage", None)
        input_details = self._usage_value(usage, "input_tokens_details")
        output_details = self._usage_value(usage, "output_tokens_details")
        actual = {
            "input_tokens": self._usage_value(usage, "input_tokens"),
            "output_tokens": self._usage_value(usage, "output_tokens"),
            "total_tokens": self._usage_value(usage, "total_tokens"),
            "cached_input_tokens": self._usage_value(
                input_details, "cached_tokens", default=0
            ),
            "reasoning_output_tokens": self._usage_value(
                output_details, "reasoning_tokens", default=0
            ),
            "attempt_count": 1,
        }
        return parsed, actual

    async def _parse_anthropic(
        self,
        profile: ModelProfile,
        system: str,
        user: str,
        response_model: type[T],
    ) -> tuple[T, dict[str, Any]]:
        if AsyncAnthropic is None:
            raise LLMConfigurationError(
                "The anthropic package is required for provider=anthropic"
            )
        api_key = self._api_key(profile)
        if not api_key:
            raise LLMConfigurationError(
                "ANTHROPIC_API_KEY is required for provider=anthropic"
            )

        client = AsyncAnthropic(
            api_key=api_key,
            base_url=profile.base_url or self.settings.anthropic_base_url,
            timeout=profile.timeout_seconds,
            max_retries=profile.max_retries,
        )
        output_config: dict[str, Any] = {
            "format": {
                "type": "json_schema",
                "schema": response_model.model_json_schema(),
            }
        }
        if profile.reasoning_effort not in (None, "none"):
            output_config["effort"] = profile.reasoning_effort

        request: dict[str, Any] = {
            "model": profile.model,
            "max_tokens": profile.max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        if profile.anthropic.thinking == "adaptive":
            request["thinking"] = {"type": "adaptive"}
        if profile.stop_sequences:
            request["stop_sequences"] = profile.stop_sequences
        if profile.anthropic.sampling_mode == "legacy":
            if profile.temperature is not None:
                request["temperature"] = profile.temperature
            elif profile.top_p is not None:
                request["top_p"] = profile.top_p
            elif profile.anthropic.top_k is not None:
                request["top_k"] = profile.anthropic.top_k

        try:
            response = await client.messages.create(**request)
        finally:
            await client.close()

        text_blocks = [
            str(getattr(block, "text", ""))
            for block in getattr(response, "content", [])
            if getattr(block, "type", None) == "text" and getattr(block, "text", None)
        ]
        content = "".join(text_blocks).strip()
        if not content:
            stop_reason = getattr(response, "stop_reason", None)
            raise RuntimeError(
                f"Anthropic model {profile.model!r} returned no structured text "
                f"(stop_reason={stop_reason!r})"
            )
        try:
            parsed = response_model.model_validate_json(content)
        except ValueError as exc:
            raise RuntimeError(
                f"Anthropic model {profile.model!r} returned invalid structured output: {exc}"
            ) from exc

        usage = getattr(response, "usage", None)
        input_tokens = self._usage_value(usage, "input_tokens")
        output_tokens = self._usage_value(usage, "output_tokens")
        total_tokens = (
            int(input_tokens) + int(output_tokens)
            if input_tokens is not None and output_tokens is not None
            else None
        )
        return parsed, {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cached_input_tokens": self._usage_value(
                usage, "cache_read_input_tokens", default=0
            ),
            "reasoning_output_tokens": 0,
            "attempt_count": 1,
        }

    async def _parse_ollama(
        self,
        profile: ModelProfile,
        system: str,
        user: str,
        response_model: type[T],
    ) -> tuple[T, dict[str, Any]]:
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
            parsed = response_model.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(
                f"Ollama model {profile.model!r} returned invalid structured output: {exc}"
            ) from exc
        input_tokens = response_payload.get("prompt_eval_count")
        output_tokens = response_payload.get("eval_count")
        total_tokens = (
            int(input_tokens) + int(output_tokens)
            if input_tokens is not None and output_tokens is not None
            else None
        )
        return parsed, {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cached_input_tokens": 0,
            "reasoning_output_tokens": 0,
            "duration_ms": (response_payload.get("total_duration") or 0) / 1_000_000
            if response_payload.get("total_duration") is not None
            else None,
            "attempt_count": attempt + 1,
        }

    @staticmethod
    def _usage_value(value: Any, key: str, default: Any = None) -> Any:
        if value is None:
            return default
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)


class StructuredLLMFactory:
    def __init__(self, settings: Settings, registry: AgentModelRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self.limiter = asyncio.Semaphore(settings.max_concurrent_llm_calls)

    def for_agent(self, agent_name: str) -> StructuredLLM:
        return StructuredLLM(
            self.settings,
            agent_name=agent_name,
            registry=self.registry,
            limiter=self.limiter,
        )
