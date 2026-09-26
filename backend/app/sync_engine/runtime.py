"""同步引擎的进程内装配。

测试环境用内存存储，避免单测去连 Redis。生产路径在第一次调用时创建 Redis 客户端。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.adapters.ratelimit import TokenBucketLimiter, decode_bucket, encode_bucket
from app.core.config import settings
from app.core.logging import get_logger
from app.sync_engine.errors import StoreUnavailable
from app.sync_engine.idempotency import BloomFilter, MemoryBitStore, RedisBitStore
from app.sync_engine.locks import BeatLock, LockStatus, MemoryLockStore, RedisLockStore
from app.sync_engine.policy import SyncPolicy, policy_from_settings
from app.sync_engine.resilience import (
    CircuitBreaker,
    DeadLetterQueue,
    MemoryLetterList,
    RedisLetterList,
    decode_circuit,
    encode_circuit,
)
from app.sync_engine.state_store import JsonStateStore, MemoryJsonKv, RedisJsonKv

log = get_logger(__name__)

_policy: SyncPolicy | None = None
_limiter: TokenBucketLimiter | None = None
_breaker: CircuitBreaker | None = None
_bloom: BloomFilter | None = None
_lock: BeatLock | None = None
_dlq: DeadLetterQueue | None = None


def reset_runtime() -> None:
    """测试隔离：丢掉已经装配的单例。"""
    global _policy, _limiter, _breaker, _bloom, _lock, _dlq
    _policy = None
    _limiter = None
    _breaker = None
    _bloom = None
    _lock = None
    _dlq = None


def get_policy() -> SyncPolicy:
    global _policy
    if _policy is None:
        _policy = policy_from_settings()
    return _policy


def get_rate_limiter() -> TokenBucketLimiter:
    global _limiter
    if _limiter is None:
        policy = get_policy()
        kv = MemoryJsonKv() if settings.is_test else RedisJsonKv(settings.redis_url)
        store = JsonStateStore(
            kv,
            ttl_seconds=policy.state_ttl_seconds,
            encode=encode_bucket,
            decode=decode_bucket,
        )
        _limiter = TokenBucketLimiter(store, policy)
    return _limiter


def get_circuit_breaker() -> CircuitBreaker:
    global _breaker
    if _breaker is None:
        policy = get_policy()
        kv = MemoryJsonKv() if settings.is_test else RedisJsonKv(settings.redis_url)
        store = JsonStateStore(
            kv,
            ttl_seconds=policy.state_ttl_seconds,
            encode=encode_circuit,
            decode=decode_circuit,
        )
        _breaker = CircuitBreaker(store, policy)
    return _breaker


def get_bloom() -> BloomFilter:
    global _bloom
    if _bloom is None:
        policy = get_policy()
        bits = MemoryBitStore(policy.bloom_bit_size) if settings.is_test else RedisBitStore(settings.redis_url)
        _bloom = BloomFilter(bits, bit_size=policy.bloom_bit_size, hash_count=policy.bloom_hash_count)
    return _bloom


def get_beat_lock() -> BeatLock:
    global _lock
    if _lock is None:
        store = MemoryLockStore() if settings.is_test else RedisLockStore(settings.redis_url)
        _lock = BeatLock(store)
    return _lock


def get_dead_letter_queue() -> DeadLetterQueue:
    global _dlq
    if _dlq is None:
        letters = MemoryLetterList() if settings.is_test else RedisLetterList(settings.redis_url)
        _dlq = DeadLetterQueue(letters)
    return _dlq


async def run_beat_singleton(
    name: str,
    body: Callable[[], Awaitable[dict[str, Any]]],
    *,
    lock: BeatLock | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """拿到锁才执行。锁被占用就跳过；存储不可用则照常执行，由幂等键兜底。"""
    active = lock if lock is not None else get_beat_lock()
    ttl = get_policy().beat_lock_ttl_seconds if ttl_seconds is None else ttl_seconds
    try:
        outcome = await active.acquire(name, ttl)
    except StoreUnavailable:
        log.warning("beat_lock_unavailable", lock=name)
        return await body()
    if outcome.status is LockStatus.HELD:
        log.info("beat_lock_held", lock=name)
        return {"status": "skipped", "reason": "lock_held"}
    try:
        return await body()
    finally:
        if outcome.token is not None:
            try:
                await active.release(name, outcome.token)
            except StoreUnavailable:
                log.warning("beat_lock_release_failed", lock=name)


__all__ = [
    "get_beat_lock",
    "get_bloom",
    "get_circuit_breaker",
    "get_dead_letter_queue",
    "get_policy",
    "get_rate_limiter",
    "reset_runtime",
    "run_beat_singleton",
]
