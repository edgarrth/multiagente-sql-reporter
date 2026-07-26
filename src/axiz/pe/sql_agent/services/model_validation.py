from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - runtime dependency in the API image
    AsyncOpenAI = None  # type: ignore[assignment,misc]

from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.models.contracts import (
    ModelValidationItem,
    ModelValidationReport,
)
from axiz.pe.sql_agent.services.llm import AgentModelRegistry, ModelProfile


class ModelCatalogValidationError(RuntimeError):
    pass


class _ProbeResponse(BaseModel):
    ok: bool


class ModelCatalogValidator:
    """Actively validates configured model IDs and Structured Outputs support.

    Catalog lookup and the optional probe are deliberately separated. OpenAI-compatible
    gateways sometimes accept private aliases without listing them in `/v1/models`; in that
    case a successful structured-output probe is authoritative and the missing catalog entry
    is retained as a warning.
    """

    def __init__(self, settings: Settings, registry: AgentModelRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self._cached: ModelValidationReport | None = None

    @property
    def report(self) -> ModelValidationReport | None:
        return self._cached

    async def validate(self, *, force: bool = False) -> ModelValidationReport:
        now = datetime.now(UTC)
        if (
            not force
            and self._cached is not None
            and now - self._cached.checked_at
            < timedelta(seconds=self.settings.model_validation_cache_ttl_seconds)
        ):
            return self._cached

        mode = self.settings.model_validation_mode
        if mode == "off":
            report = ModelValidationReport(
                mode=mode,
                failure_policy=self.settings.model_validation_failure_policy,
                ready=True,
                checked_at=now,
                skipped_count=len(self.registry.list_profiles()),
                unique_model_count=0,
                items=[],
            )
            self._cached = report
            return report

        profiles: dict[tuple[str, str, str], tuple[str, ModelProfile]] = {}
        for agent in sorted(self.registry.document.agents):
            profile = self.registry.profile_for(agent)
            key = (profile.provider, profile.base_url or "", profile.model)
            profiles.setdefault(key, (agent, profile))

        items: list[ModelValidationItem] = []
        for agent, profile in profiles.values():
            if profile.provider == "openai":
                item = await self._validate_openai(agent, profile, probe=mode == "probe")
            else:
                item = await self._validate_ollama(agent, profile, probe=mode == "probe")
            items.append(item)

        invalid_count = sum(item.status == "invalid" for item in items)
        warning_count = sum(item.status == "warning" for item in items)
        valid_count = sum(item.status == "valid" for item in items)
        skipped_count = sum(item.status == "skipped" for item in items)
        report = ModelValidationReport(
            mode=mode,
            failure_policy=self.settings.model_validation_failure_policy,
            ready=invalid_count == 0,
            checked_at=now,
            unique_model_count=len(items),
            valid_count=valid_count,
            warning_count=warning_count,
            invalid_count=invalid_count,
            skipped_count=skipped_count,
            items=items,
        )
        self._cached = report
        if invalid_count and self.settings.model_validation_failure_policy == "fail":
            details = "; ".join(
                f"{item.provider}/{item.model}: {item.error}"
                for item in items
                if item.status == "invalid"
            )
            raise ModelCatalogValidationError(
                f"Configured model validation failed: {details}"
            )
        return report

    def _api_key(self, profile: ModelProfile) -> str | None:
        import os

        if profile.api_key_env:
            return os.getenv(profile.api_key_env)
        secret = (
            self.settings.openai_api_key
            if profile.provider == "openai"
            else self.settings.ollama_api_key
        )
        return secret.get_secret_value() if secret else None

    async def _validate_openai(
        self, agent: str, profile: ModelProfile, *, probe: bool
    ) -> ModelValidationItem:
        started = time.perf_counter()
        if AsyncOpenAI is None:
            return self._invalid(agent, profile, started, "openai package is not installed")
        api_key = self._api_key(profile)
        if not api_key:
            return self._invalid(agent, profile, started, "OPENAI_API_KEY is not configured")

        client = AsyncOpenAI(
            api_key=api_key,
            base_url=profile.base_url or self.settings.openai_base_url,
            timeout=self.settings.model_validation_timeout_seconds,
            max_retries=0,
        )
        catalog_available: bool | None = None
        warnings: list[str] = []
        try:
            try:
                await client.models.retrieve(profile.model)
                catalog_available = True
            except Exception as exc:
                catalog_available = False
                warnings.append(f"Model catalog lookup failed: {exc}")

            structured_supported: bool | None = None
            if probe:
                try:
                    response = await client.responses.parse(
                        model=profile.model,
                        input=[
                            {"role": "system", "content": "Return the requested JSON only."},
                            {"role": "user", "content": "Return ok=true."},
                        ],
                        text_format=_ProbeResponse,
                        max_output_tokens=256,
                        store=False,
                    )
                    parsed = response.output_parsed
                    structured_supported = bool(parsed and parsed.ok is True)
                    if not structured_supported:
                        raise RuntimeError("probe returned no valid structured output")
                except Exception as exc:
                    return self._invalid(
                        agent,
                        profile,
                        started,
                        f"Structured Outputs probe failed: {exc}",
                        catalog_available=catalog_available,
                        warnings=warnings,
                    )

            if catalog_available is False and structured_supported:
                status = "warning"
                warnings.append(
                    "The model alias is callable but is not exposed by the provider catalog."
                )
            elif catalog_available is False:
                return self._invalid(
                    agent,
                    profile,
                    started,
                    "The configured model is not available in the provider catalog.",
                    catalog_available=False,
                    warnings=warnings,
                )
            else:
                status = "valid"
            return ModelValidationItem(
                agent=agent,
                provider=profile.provider,
                model=profile.model,
                status=status,
                catalog_available=catalog_available,
                structured_output_supported=structured_supported,
                context_limit_tokens=profile.model_context_limit_tokens,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                warnings=warnings,
            )
        finally:
            await client.close()

    async def _validate_ollama(
        self, agent: str, profile: ModelProfile, *, probe: bool
    ) -> ModelValidationItem:
        started = time.perf_counter()
        base_url = (profile.base_url or self.settings.ollama_base_url).rstrip("/")
        headers = {"Content-Type": "application/json"}
        api_key = self._api_key(profile)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        timeout = self.settings.model_validation_timeout_seconds
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{base_url}/api/show",
                    headers=headers,
                    json={"model": profile.model, "verbose": False},
                )
                if response.status_code == 404:
                    return self._invalid(
                        agent,
                        profile,
                        started,
                        "The configured Ollama model is not installed or available.",
                        catalog_available=False,
                    )
                response.raise_for_status()
                details = response.json()
                context_limit = self._ollama_context_limit(details) or profile.model_context_limit_tokens
                structured_supported: bool | None = None
                if probe:
                    probe_response = await client.post(
                        f"{base_url}/api/chat",
                        headers=headers,
                        json={
                            "model": profile.model,
                            "messages": [{"role": "user", "content": "Return ok=true."}],
                            "format": _ProbeResponse.model_json_schema(),
                            "stream": False,
                            "options": {"num_predict": 64, "temperature": 0},
                        },
                    )
                    probe_response.raise_for_status()
                    content = probe_response.json().get("message", {}).get("content")
                    structured_supported = bool(
                        content and _ProbeResponse.model_validate_json(content).ok is True
                    )
                    if not structured_supported:
                        return self._invalid(
                            agent,
                            profile,
                            started,
                            "Ollama structured-output probe returned an invalid payload.",
                            catalog_available=True,
                        )
                warnings: list[str] = []
                if context_limit < profile.context_window_tokens:
                    warnings.append(
                        "Configured context_window_tokens exceeds the context reported by Ollama."
                    )
                status = "warning" if warnings else "valid"
                return ModelValidationItem(
                    agent=agent,
                    provider=profile.provider,
                    model=profile.model,
                    status=status,
                    catalog_available=True,
                    structured_output_supported=structured_supported,
                    context_limit_tokens=context_limit,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    warnings=warnings,
                )
        except Exception as exc:
            return self._invalid(agent, profile, started, str(exc))

    @staticmethod
    def _ollama_context_limit(payload: dict[str, Any]) -> int | None:
        model_info = payload.get("model_info") or {}
        for key, value in model_info.items():
            if str(key).endswith(".context_length") and isinstance(value, int):
                return value
        return None

    @staticmethod
    def _invalid(
        agent: str,
        profile: ModelProfile,
        started: float,
        error: str,
        *,
        catalog_available: bool | None = None,
        warnings: list[str] | None = None,
    ) -> ModelValidationItem:
        return ModelValidationItem(
            agent=agent,
            provider=profile.provider,
            model=profile.model,
            status="invalid",
            catalog_available=catalog_available,
            structured_output_supported=False,
            context_limit_tokens=profile.model_context_limit_tokens,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            warnings=warnings or [],
            error=error,
        )
