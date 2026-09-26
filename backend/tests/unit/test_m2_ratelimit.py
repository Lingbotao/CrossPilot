"""M2-01 令牌桶：按店铺分桶、app 维度共享、429 降速、存储不可用时拒绝放行。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from redis.exceptions import WatchError

from app.adapters.base import RateLimitSpec
from app.adapters.quotas import quota_for
from app.adapters.ratelimit import (
    TokenBucketLimiter,
    bucket_key,
    decode_bucket,
    encode_bucket,
)
from app.sync_engine.errors import StoreUnavailable
from app.sync_engine.policy import SyncPolicy
from app.sync_engine.state_store import JsonStateStore, MemoryJsonKv, RedisJsonKv


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _limiter(policy: SyncPolicy | None = None, clock: Clock | None = None) -> TokenBucketLimiter:
    store = JsonStateStore(MemoryJsonKv(), ttl_seconds=60, encode=encode_bucket, decode=decode_bucket)
    return TokenBucketLimiter(store, policy or SyncPolicy(), clock=clock)


def test_bucket_key_follows_dimension() -> None:
    assert bucket_key("Shopee", "9", quota_for("shopee")) == "rl:shopee:9"
    assert bucket_key("lazada", "9", quota_for("lazada")) == "rl:lazada:app"


async def test_burst_then_refill_and_shops_are_independent() -> None:
    clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
    limiter = _limiter(clock=clock)
    spec = quota_for("amazon")
    for _ in range(spec.burst):
        assert (await limiter.try_acquire("amazon", "1", spec)).allowed
    assert not (await limiter.try_acquire("amazon", "1", spec)).allowed
    assert (await limiter.try_acquire("amazon", "2", spec)).allowed
    clock.advance(1)
    assert (await limiter.try_acquire("amazon", "1", spec)).allowed
    assert not (await limiter.try_acquire("amazon", "1", spec)).allowed


async def test_app_dimension_shares_one_bucket() -> None:
    limiter = _limiter(clock=Clock(datetime(2026, 1, 1, tzinfo=UTC)))
    spec = quota_for("lazada")
    for _ in range(spec.burst):
        assert (await limiter.try_acquire("lazada", "shop-a", spec)).allowed
    assert not (await limiter.try_acquire("lazada", "shop-b", spec)).allowed


async def test_parallel_acquires_stop_at_burst() -> None:
    spec = RateLimitSpec(qps=10, burst=5, dimension="shop", batch_limit=1)
    limiter = _limiter(clock=Clock(datetime(2026, 1, 1, tzinfo=UTC)))
    results = await asyncio.gather(*[limiter.try_acquire("shopee", "9", spec) for _ in range(20)])
    assert sum(1 for item in results if item.allowed) == spec.burst


async def test_repeated_429_halves_rate_until_floor_then_recovers() -> None:
    clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
    policy = SyncPolicy(slowdown_ratio=0.5, penalty_floor=0.25, recover_after_successes=2)
    limiter = _limiter(policy, clock)
    spec = RateLimitSpec(qps=10, burst=10, dimension="shop", batch_limit=1)
    assert await limiter.record_throttled("shopee", "1", spec) == pytest.approx(0.5)
    assert await limiter.record_throttled("shopee", "1", spec) == pytest.approx(0.25)
    assert await limiter.record_throttled("shopee", "1", spec) == pytest.approx(0.25)
    granted = 0
    for _ in range(8):
        if (await limiter.try_acquire("shopee", "1", spec)).allowed:
            granted += 1
    assert granted == 2
    clock.advance(1)
    granted = 0
    for _ in range(8):
        if (await limiter.try_acquire("shopee", "1", spec)).allowed:
            granted += 1
    # 0.25 倍率下每秒只补 2.5 个令牌，1 秒内仍然只能放行 2 次。
    assert granted == 2
    assert await limiter.record_success("shopee", "1", spec) == pytest.approx(0.25)
    assert await limiter.record_success("shopee", "1", spec) == pytest.approx(0.5)
    assert await limiter.record_success("shopee", "1", spec) == pytest.approx(0.5)
    assert await limiter.record_success("shopee", "1", spec) == pytest.approx(1)


async def test_store_outage_denies_instead_of_calling_platform() -> None:
    class DownKv:
        async def get(self, key: str) -> str | None:
            del key
            raise StoreUnavailable("down")

        async def compare_set(self, key: str, expected: str | None, value: str, ttl_seconds: int) -> bool:
            del key, expected, value, ttl_seconds
            return False

    store = JsonStateStore(DownKv(), ttl_seconds=10, encode=encode_bucket, decode=decode_bucket)
    limiter = TokenBucketLimiter(store, SyncPolicy())
    decision = await limiter.try_acquire("amazon", "1", quota_for("amazon"))
    assert decision.allowed is False
    assert decision.retry_after_seconds == 1


class _FakePipe:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._snapshot: str | None = None
        self._watched = False
        self._ops: list[tuple[str, str]] = []

    async def __aenter__(self) -> _FakePipe:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def watch(self, key: str) -> None:
        del key
        self._watched = True
        self._snapshot = self._redis.value

    async def get(self, key: str) -> str | None:
        del key
        return self._redis.value

    def multi(self) -> None:
        return None

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        del key, ex
        self._ops.append(("set", value))

    async def execute(self) -> list[object]:
        if self._watched and self._redis.fail_watches:
            self._redis.fail_watches -= 1
            raise WatchError("conflict")
        if self._snapshot != self._redis.value and self._ops:
            raise WatchError("changed")
        for _op, value in self._ops:
            self._redis.value = value
        return []


class _FakeRedis:
    def __init__(self) -> None:
        self.value: str | None = None
        self.fail_watches = 0

    def pipeline(self, transaction: bool = True) -> _FakePipe:
        del transaction
        return _FakePipe(self)

    async def get(self, key: str) -> str | None:
        del key
        return self.value


async def test_redis_bucket_retries_watch_conflict() -> None:
    fake = _FakeRedis()
    fake.fail_watches = 1
    store = JsonStateStore(
        RedisJsonKv("redis://unused", client=fake),
        ttl_seconds=30,
        encode=encode_bucket,
        decode=decode_bucket,
    )
    limiter = TokenBucketLimiter(store, SyncPolicy(), clock=Clock(datetime(2026, 1, 1, tzinfo=UTC)))
    spec = RateLimitSpec(qps=1, burst=1, dimension="shop", batch_limit=1)
    assert (await limiter.try_acquire("tiktok", "4", spec)).allowed
    assert fake.value is not None
    assert decode_bucket(fake.value).tokens == pytest.approx(0)


async def test_corrupt_bucket_json_is_replaced() -> None:
    kv = MemoryJsonKv()
    await kv.compare_set("rl:shopee:1", None, "{", 10)
    store = JsonStateStore(kv, ttl_seconds=10, encode=encode_bucket, decode=decode_bucket)
    limiter = TokenBucketLimiter(store, SyncPolicy(), clock=Clock(datetime(2026, 1, 1, tzinfo=UTC)))
    spec = RateLimitSpec(qps=1, burst=1, dimension="shop", batch_limit=1)
    assert (await limiter.try_acquire("shopee", "1", spec)).allowed
