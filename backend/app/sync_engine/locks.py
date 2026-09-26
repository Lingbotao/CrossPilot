"""Beat 分布式锁（M2-02）。

多实例 Beat 同时触发时，只有拿到锁的那一个执行。锁带 TTL，进程崩溃后不会一直占着。
Redis 不可用时 ``acquire`` 抛出 ``StoreUnavailable``，调用方继续执行，由幂等键挡住重复入库。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from app.sync_engine.errors import StoreUnavailable

_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

BEAT_SCAN_DUE_SHOPS = "beat:scan_due_shops"
BEAT_REFRESH_CREDENTIALS = "beat:refresh_credentials"
BEAT_SCAN_DEAD_LETTER = "beat:scan_dead_letter"


class LockStatus(StrEnum):
    ACQUIRED = "acquired"
    HELD = "held"


@dataclass(frozen=True, slots=True)
class LockOutcome:
    status: LockStatus
    token: str | None = None


@dataclass(slots=True)
class _Hold:
    token: str
    expires_at: datetime


class MemoryLockStore:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._held: dict[str, _Hold] = {}

    async def acquire(self, name: str, token: str, ttl_seconds: int) -> bool:
        now = self._clock()
        current = self._held.get(name)
        if current is not None and current.expires_at > now:
            return False
        self._held[name] = _Hold(token=token, expires_at=now + timedelta(seconds=ttl_seconds))
        return True

    async def release(self, name: str, token: str) -> None:
        current = self._held.get(name)
        if current is not None and current.token == token:
            del self._held[name]


class RedisLockStore:
    def __init__(self, url: str, *, client: Any | None = None) -> None:
        self._url = url
        self._client = client

    async def _redis(self) -> Any:
        if self._client is None:
            from redis.asyncio import Redis

            self._client = Redis.from_url(self._url, decode_responses=True)
        return self._client

    async def acquire(self, name: str, token: str, ttl_seconds: int) -> bool:
        client = await self._redis()
        ok = await client.set(_key(name), token, nx=True, ex=ttl_seconds)
        return bool(ok)

    async def release(self, name: str, token: str) -> None:
        client = await self._redis()
        await client.eval(_RELEASE_SCRIPT, 1, _key(name), token)


def _key(name: str) -> str:
    return f"lock:{name}"


class BeatLock:
    def __init__(self, store: MemoryLockStore | RedisLockStore) -> None:
        self._store = store

    async def acquire(self, name: str, ttl_seconds: int) -> LockOutcome:
        token = secrets.token_hex(16)
        try:
            got = await self._store.acquire(name, token, ttl_seconds)
        except _lock_errors() as exc:
            raise StoreUnavailable(str(exc)) from exc
        if got:
            return LockOutcome(status=LockStatus.ACQUIRED, token=token)
        return LockOutcome(status=LockStatus.HELD)

    async def release(self, name: str, token: str) -> None:
        try:
            await self._store.release(name, token)
        except _lock_errors() as exc:
            raise StoreUnavailable(str(exc)) from exc


def _lock_errors() -> tuple[type[BaseException], ...]:
    import redis.exceptions

    return (redis.exceptions.RedisError, TimeoutError, OSError)


__all__ = [
    "BEAT_REFRESH_CREDENTIALS",
    "BEAT_SCAN_DEAD_LETTER",
    "BEAT_SCAN_DUE_SHOPS",
    "BeatLock",
    "LockOutcome",
    "LockStatus",
    "MemoryLockStore",
    "RedisLockStore",
]
