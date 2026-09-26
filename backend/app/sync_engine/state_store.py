"""带乐观并发控制的 JSON 状态存储。

令牌桶和熔断器共用这一层：先读出当前 JSON，在进程内算出新状态，再用
compare-and-set 写回。写失败就重读重算，避免两个 Worker 同时把同一个令牌扣两次。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

from app.core.logging import get_logger
from app.sync_engine.errors import StoreUnavailable

log = get_logger(__name__)

_MAX_ATTEMPTS = 8


class JsonKv(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def compare_set(self, key: str, expected: str | None, value: str, ttl_seconds: int) -> bool: ...


class MemoryJsonKv:
    """单测与 ``APP_ENV=test`` 使用的进程内实现。"""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock: asyncio.Lock | None = None

    def _guard(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def get(self, key: str) -> str | None:
        async with self._guard():
            return self._values.get(key)

    async def compare_set(self, key: str, expected: str | None, value: str, ttl_seconds: int) -> bool:
        del ttl_seconds
        async with self._guard():
            if self._values.get(key) != expected:
                return False
            self._values[key] = value
            return True


class RedisJsonKv:
    """Redis 实现。``WATCH`` + ``MULTI`` 保证同一键的读改写是原子的。"""

    def __init__(self, url: str, *, client: Any | None = None) -> None:
        self._url = url
        self._client = client

    async def _redis(self) -> Any:
        if self._client is None:
            from redis.asyncio import Redis

            self._client = Redis.from_url(self._url, decode_responses=True)
        return self._client

    async def get(self, key: str) -> str | None:
        try:
            client = await self._redis()
            value = await client.get(key)
        except _connection_errors() as exc:
            raise StoreUnavailable(str(exc)) from exc
        if value is None:
            return None
        return str(value)

    async def compare_set(self, key: str, expected: str | None, value: str, ttl_seconds: int) -> bool:
        from redis.exceptions import WatchError

        try:
            client = await self._redis()
            async with client.pipeline(transaction=True) as pipe:
                await pipe.watch(key)
                current = await pipe.get(key)
                if current != expected:
                    return False
                pipe.multi()
                pipe.set(key, value, ex=ttl_seconds)
                await pipe.execute()
                return True
        except WatchError:
            return False
        except _connection_errors() as exc:
            raise StoreUnavailable(str(exc)) from exc


def _connection_errors() -> tuple[type[BaseException], ...]:
    import redis.exceptions

    return (redis.exceptions.RedisError, TimeoutError, OSError)


class JsonStateStore:
    """把纯函数算出的新状态写回 ``JsonKv``。"""

    def __init__(
        self,
        kv: JsonKv,
        *,
        ttl_seconds: int,
        encode: Callable[[Any], str],
        decode: Callable[[str], Any],
    ) -> None:
        self._kv = kv
        self._ttl = ttl_seconds
        self._encode = encode
        self._decode = decode

    async def update(self, key: str, mutator: Callable[[Any | None], Any]) -> Any:
        for _ in range(_MAX_ATTEMPTS):
            raw = await self._kv.get(key)
            state: Any | None
            try:
                state = self._decode(raw) if raw is not None else None
            except (ValueError, KeyError, TypeError):
                log.warning("sync_state_corrupt", key=key)
                state = None
            new_state = mutator(state)
            payload = self._encode(new_state)
            if await self._kv.compare_set(key, raw, payload, self._ttl):
                return new_state
        raise StoreUnavailable(f"状态写入冲突过多: {key}")


__all__ = ["JsonKv", "JsonStateStore", "MemoryJsonKv", "RedisJsonKv"]
