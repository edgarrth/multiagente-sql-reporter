from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol


class JsonCacheBackend(Protocol):
    async def get_json(self, key: str) -> dict[str, Any] | None: ...
    async def set_json(
        self, key: str, value: dict[str, Any], ttl_seconds: int = 3600
    ) -> None: ...
    async def delete(self, key: str) -> None: ...


@dataclass(frozen=True)
class CacheLookup:
    hit: bool
    key: str
    value: dict[str, Any] | None = None
    age_seconds: float | None = None


class AgentResponseCache:
    """Redis-backed semantic cache for safe, repeatable LLM work.

    The cache never stores credentials, HITL decisions or query-result rows. Callers provide a
    projection of the request and configuration. The projection is hashed, so raw prompts are not
    embedded in Redis keys. Cached values are versioned and must remain JSON serializable.
    """

    def __init__(
        self,
        backend: JsonCacheBackend,
        *,
        namespace: str = "axiz:agent-cache:v2",
        enabled: bool = True,
        default_ttl_seconds: int = 900,
    ) -> None:
        self.backend = backend
        self.namespace = namespace.rstrip(":")
        self.enabled = enabled
        self.default_ttl_seconds = default_ttl_seconds

    @staticmethod
    def fingerprint(payload: Any) -> str:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def key(self, kind: str, payload: Any) -> str:
        return f"{self.namespace}:{kind}:{self.fingerprint(payload)}"

    async def get(self, kind: str, payload: Any) -> CacheLookup:
        key = self.key(kind, payload)
        if not self.enabled:
            return CacheLookup(hit=False, key=key)
        try:
            envelope = await self.backend.get_json(key)
        except Exception:
            return CacheLookup(hit=False, key=key)
        if not envelope:
            return CacheLookup(hit=False, key=key)
        created_at = float(envelope.get("created_at") or 0.0)
        value = envelope.get("value")
        if not isinstance(value, dict):
            return CacheLookup(hit=False, key=key)
        return CacheLookup(
            hit=True,
            key=key,
            value=value,
            age_seconds=max(0.0, time.time() - created_at) if created_at else None,
        )

    async def set(
        self,
        kind: str,
        payload: Any,
        value: dict[str, Any],
        *,
        ttl_seconds: int | None = None,
    ) -> str:
        key = self.key(kind, payload)
        if not self.enabled:
            return key
        envelope = {
            "created_at": time.time(),
            "value": value,
        }
        try:
            await self.backend.set_json(
                key,
                envelope,
                ttl_seconds=(
                    self.default_ttl_seconds
                    if ttl_seconds is None
                    else max(1, int(ttl_seconds))
                ),
            )
        except Exception:
            # Cache failure must not make the regulated execution path unavailable.
            pass
        return key

    async def invalidate(self, kind: str, payload: Any) -> None:
        if not self.enabled:
            return
        try:
            await self.backend.delete(self.key(kind, payload))
        except Exception:
            pass


class InMemoryJsonCache:
    """Small async backend used by tests and local eval harnesses."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[float, dict[str, Any]]] = {}

    async def get_json(self, key: str) -> dict[str, Any] | None:
        item = self._values.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.time():
            self._values.pop(key, None)
            return None
        return json.loads(json.dumps(value, default=str))

    async def set_json(
        self, key: str, value: dict[str, Any], ttl_seconds: int = 3600
    ) -> None:
        self._values[key] = (
            time.time() + max(1, int(ttl_seconds)),
            json.loads(json.dumps(value, default=str)),
        )

    async def delete(self, key: str) -> None:
        self._values.pop(key, None)
