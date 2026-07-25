from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis


class RedisStore:
    def __init__(self, url: str) -> None:
        self.client = Redis.from_url(url, decode_responses=True)

    async def ping(self) -> bool:
        return bool(await self.client.ping())

    async def get_json(self, key: str) -> dict[str, Any] | None:
        value = await self.client.get(key)
        return json.loads(value) if value else None

    async def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int = 3600) -> None:
        await self.client.set(key, json.dumps(value, default=str), ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self.client.delete(key)

    async def close(self) -> None:
        await self.client.aclose()
