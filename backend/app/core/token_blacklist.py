"""JWT ``jti`` 撤销清单（PRD A-02：主动登出失效 + Refresh 轮换）。

Access / Refresh 都是自包含签名，服务端默认无状态。要让「登出」和
「Refresh 轮换后旧令牌作废」立刻生效，必须把 ``jti`` 记进一份带 TTL 的
黑名单：TTL = 该令牌剩余寿命，过期后自然不必再记。

存储分两层，缺一不可：

- **进程内 dict**：单测、``./dev.sh`` 无 Redis、同进程立刻生效。
- **Redis**（``REDIS_URL``）：多 Worker 共享。连不上 / 读写失败只打日志，
  **不把所有令牌判死**（否则没起 Redis 的开发机一次登录都进不去）。
"""

from __future__ import annotations

import time
from typing import Protocol

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import TokenPayload

log = get_logger(__name__)

_KEY_PREFIX = "auth:jti:revoked:"


class TokenBlacklist(Protocol):
    async def revoke(self, jti: str, ttl_seconds: int) -> None: ...

    async def is_revoked(self, jti: str) -> bool: ...

    def clear(self) -> None: ...


class InMemoryTokenBlacklist:
    """进程内实现。key → 过期时间戳（epoch seconds）。"""

    def __init__(self) -> None:
        self._until: dict[str, float] = {}

    async def revoke(self, jti: str, ttl_seconds: int) -> None:
        if not jti:
            return
        self._until[jti] = time.time() + max(1, ttl_seconds)

    async def is_revoked(self, jti: str) -> bool:
        expires = self._until.get(jti)
        if expires is None:
            return False
        if expires <= time.time():
            del self._until[jti]
            return False
        return True

    def clear(self) -> None:
        self._until.clear()


class RedisBackedTokenBlacklist:
    """内存必写；非 test 环境再尽力同步到 Redis。"""

    def __init__(self, memory: InMemoryTokenBlacklist) -> None:
        self._memory = memory
        self._client: object | None = None
        self._disabled = False

    async def _redis(self) -> object | None:
        if settings.is_test or self._disabled:
            return None
        if self._client is not None:
            return self._client
        try:
            from redis.asyncio import Redis

            client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
            await client.ping()
            self._client = client
            return client
        except Exception as exc:
            self._disabled = True
            log.warning("token_blacklist_redis_unavailable", error=str(exc)[:200])
            return None

    async def revoke(self, jti: str, ttl_seconds: int) -> None:
        await self._memory.revoke(jti, ttl_seconds)
        client = await self._redis()
        if client is None:
            return
        try:
            await client.set(_KEY_PREFIX + jti, "1", ex=max(1, ttl_seconds))  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("token_blacklist_redis_revoke_failed", error=str(exc)[:200])

    async def is_revoked(self, jti: str) -> bool:
        if await self._memory.is_revoked(jti):
            return True
        client = await self._redis()
        if client is None:
            return False
        try:
            return bool(await client.exists(_KEY_PREFIX + jti))  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning("token_blacklist_redis_check_failed", error=str(exc)[:200])
            return False

    def clear(self) -> None:
        self._memory.clear()
        self._client = None
        self._disabled = False


_memory = InMemoryTokenBlacklist()
_store: TokenBlacklist = RedisBackedTokenBlacklist(_memory)


def get_token_blacklist() -> TokenBlacklist:
    return _store


def reset_token_blacklist() -> None:
    """仅供测试：清空内存条目并丢掉 Redis 连接缓存。"""
    _store.clear()


def remaining_ttl_seconds(payload: TokenPayload, *, now: int | None = None) -> int:
    """令牌剩余寿命（秒），至少 1 秒，避免 Redis ``EX 0`` 被拒绝。"""
    epoch = int(time.time()) if now is None else now
    return max(1, int(payload.exp) - epoch)


async def revoke_payload(payload: TokenPayload) -> None:
    await get_token_blacklist().revoke(payload.jti, remaining_ttl_seconds(payload))


async def assert_token_not_revoked(payload: TokenPayload) -> None:
    """已登出 / 已轮换的 ``jti`` 一律按未认证处理，不暴露「被撤销」细节。"""
    from app.core.errors import UnauthenticatedError

    if await get_token_blacklist().is_revoked(payload.jti):
        raise UnauthenticatedError("令牌已失效")


__all__ = [
    "InMemoryTokenBlacklist",
    "RedisBackedTokenBlacklist",
    "TokenBlacklist",
    "assert_token_not_revoked",
    "get_token_blacklist",
    "remaining_ttl_seconds",
    "reset_token_blacklist",
    "revoke_payload",
]
