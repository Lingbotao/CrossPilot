"""Webhook 事件去重和入口限流。

事件键用 SET NX，TTL 来自配置（PRD 10.5：24 小时）。
限流按平台、按秒计数，配额同样来自配置，不写死在业务判断里。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol

from app.sync_engine.errors import StoreUnavailable


class EventDedup(Protocol):
    async def claim(self, key: str, ttl_seconds: int, *, now: datetime) -> bool: ...

    async def release(self, key: str) -> None: ...


class WebhookLimiter(Protocol):
    async def allow(self, platform: str, *, now: datetime, qps: int) -> bool: ...


class MemoryEventDedup:
    def __init__(self) -> None:
        self._until: dict[str, datetime] = {}

    async def claim(self, key: str, ttl_seconds: int, *, now: datetime) -> bool:
        expiry = self._until.get(key)
        if expiry is not None and expiry > now:
            return False
        self._until[key] = now + timedelta(seconds=max(1, ttl_seconds))
        return True

    async def release(self, key: str) -> None:
        self._until.pop(key, None)


class MemoryWebhookLimiter:
    def __init__(self) -> None:
        self._counts: dict[tuple[str, int], int] = {}

    async def allow(self, platform: str, *, now: datetime, qps: int) -> bool:
        if qps < 1:
            return False
        second = int(now.timestamp())
        name = platform.strip().lower()
        self._counts = {item: count for item, count in self._counts.items() if item[1] >= second - 1}
        key = (name, second)
        used = self._counts.get(key, 0) + 1
        self._counts[key] = used
        return used <= qps


class RedisEventDedup:
    def __init__(self, url: str, *, client: Any | None = None, prefix: str = "wh:event:") -> None:
        self._url = url
        self._client = client
        self._prefix = prefix

    async def _redis(self) -> Any:
        if self._client is None:
            from redis.asyncio import Redis

            self._client = Redis.from_url(self._url, decode_responses=True)
        return self._client

    def _name(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def claim(self, key: str, ttl_seconds: int, *, now: datetime) -> bool:
        del now
        try:
            client = await self._redis()
            stored = await client.set(self._name(key), "1", nx=True, ex=max(1, ttl_seconds))
        except _redis_errors() as exc:
            raise StoreUnavailable(str(exc)) from exc
        return bool(stored)

    async def release(self, key: str) -> None:
        try:
            client = await self._redis()
            await client.delete(self._name(key))
        except _redis_errors() as exc:
            raise StoreUnavailable(str(exc)) from exc


class RedisWebhookLimiter:
    def __init__(self, url: str, *, client: Any | None = None, prefix: str = "wh:rl:") -> None:
        self._url = url
        self._client = client
        self._prefix = prefix

    async def _redis(self) -> Any:
        if self._client is None:
            from redis.asyncio import Redis

            self._client = Redis.from_url(self._url, decode_responses=True)
        return self._client

    async def allow(self, platform: str, *, now: datetime, qps: int) -> bool:
        if qps < 1:
            return False
        key = f"{self._prefix}{platform.strip().lower()}:{int(now.timestamp())}"
        try:
            client = await self._redis()
            used = int(await client.incr(key))
            if used == 1:
                await client.expire(key, 2)
        except _redis_errors() as exc:
            raise StoreUnavailable(str(exc)) from exc
        return used <= qps


def _redis_errors() -> tuple[type[BaseException], ...]:
    import redis.exceptions

    return (redis.exceptions.RedisError, TimeoutError, OSError)


__all__ = [
    "EventDedup",
    "MemoryEventDedup",
    "MemoryWebhookLimiter",
    "RedisEventDedup",
    "RedisWebhookLimiter",
    "WebhookLimiter",
]
