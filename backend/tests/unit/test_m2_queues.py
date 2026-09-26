"""M2-02 队列路由、Beat 锁和 Celery 可靠性配置。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.config import Settings
from app.sync_engine.locks import BeatLock, LockStatus, MemoryLockStore, RedisLockStore
from app.sync_engine.policy import SyncPolicy, policy_from_settings
from app.sync_engine.queues import WORKER_POOLS, SyncTaskRouter, local_dev_queues, task_queue
from app.sync_engine.runtime import run_beat_singleton


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def test_routes_manual_sync_and_platforms_apart() -> None:
    assert task_queue("sync.manual.shop_orders", platform="shopee") == "high"
    assert task_queue("sync.shop_orders", platform="shopee") == "sync.shopee"
    assert task_queue("sync.shop_orders") == "sync"
    assert task_queue("batch.ship") == "batch"
    assert task_queue("report.export") == "report"
    router = SyncTaskRouter()
    assert router.route_for_task("sync.shop_orders", kwargs={"platform": "amazon"})["queue"] == "sync.amazon"


def test_local_worker_does_not_consume_the_dlq() -> None:
    queues = local_dev_queues().split(",")
    assert "dlq" not in queues
    assert "high" in queues
    assert "sync.lazada" in queues
    assert {pool.name: pool.concurrency for pool in WORKER_POOLS} == {
        "high": 8,
        "sync": 4,
        "batch": 4,
        "report": 2,
    }


def test_compose_workers_match_pool_definitions() -> None:
    text = Path(__file__).resolve().parents[3].joinpath("docker-compose.yml").read_text()
    for pool in WORKER_POOLS:
        assert f'"-Q", "{pool.queue_arg()}"' in text
        assert f'"--concurrency", "{pool.concurrency}"' in text
    assert '"-Q", "dlq"' not in text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"slowdown_ratio": 0},
        {"penalty_floor": 0},
        {"recover_after_successes": 0},
        {"backoff_seconds": (0,)},
        {"jitter_ratio": 2},
        {"circuit_failure_ratio": 1},
        {"circuit_min_samples": 0},
        {"circuit_window": 1, "circuit_min_samples": 4},
        {"circuit_open_seconds": 0},
        {"bloom_bit_size": 2, "bloom_hash_count": 4},
        {"beat_lock_ttl_seconds": 0},
        {"state_ttl_seconds": 0},
        {"order_overlap_seconds": -1},
        {"order_initial_lookback_seconds": 0},
        {"order_interval_seconds": 0},
        {"order_max_pages": 0},
    ],
)
def test_policy_rejects_invalid_fields(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SyncPolicy(**kwargs)  # type: ignore[arg-type]


def test_empty_backoff_string_is_rejected() -> None:
    with pytest.raises(ValueError):
        policy_from_settings(Settings(sync_backoff_seconds=" , "))
    with pytest.raises(ValueError):
        SyncPolicy(backoff_seconds=())
    with pytest.raises(RuntimeError, match="同步引擎"):
        Settings(sync_backoff_seconds="0").validate_for_runtime()
    assert policy_from_settings(Settings()).backoff_seconds == (1, 4, 16)


async def test_lock_is_exclusive_until_release_or_expiry() -> None:
    clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
    lock = BeatLock(MemoryLockStore(clock))
    first = await lock.acquire("beat:scan_due_shops", 10)
    second = await lock.acquire("beat:scan_due_shops", 10)
    assert first.status is LockStatus.ACQUIRED
    assert first.token is not None
    assert second.status is LockStatus.HELD
    await lock.release("beat:scan_due_shops", "wrong")
    assert (await lock.acquire("beat:scan_due_shops", 10)).status is LockStatus.HELD
    await lock.release("beat:scan_due_shops", first.token)
    assert (await lock.acquire("beat:scan_due_shops", 10)).status is LockStatus.ACQUIRED
    clock.advance(11)
    assert (await lock.acquire("beat:scan_due_shops", 10)).status is LockStatus.ACQUIRED


async def test_overlapping_beat_skips_when_lock_is_held() -> None:
    lock = BeatLock(MemoryLockStore())
    started = asyncio.Event()
    release = asyncio.Event()

    async def body() -> dict[str, str]:
        started.set()
        await release.wait()
        return {"status": "ok"}

    running = asyncio.create_task(run_beat_singleton("beat:scan", body, lock=lock, ttl_seconds=30))
    await started.wait()
    skipped = await run_beat_singleton("beat:scan", body, lock=lock, ttl_seconds=30)
    release.set()
    finished = await running
    assert finished["status"] == "ok"
    assert skipped == {"status": "skipped", "reason": "lock_held"}


class _DownLock:
    async def acquire(self, name: str, token: str, ttl_seconds: int) -> bool:
        del name, token, ttl_seconds
        raise ConnectionError("down")

    async def release(self, name: str, token: str) -> None:
        del name, token


async def test_beat_runs_when_the_lock_store_is_down() -> None:
    async def body() -> dict[str, str]:
        return {"status": "ran"}

    result = await run_beat_singleton("beat:scan", body, lock=BeatLock(_DownLock()), ttl_seconds=5)  # type: ignore[arg-type]
    assert result["status"] == "ran"


class _LockRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, token: str, nx: bool = False, ex: int | None = None) -> bool | None:
        del ex
        if nx and key in self.values:
            return None
        self.values[key] = token
        return True

    async def eval(self, script: str, nkeys: int, key: str, token: str) -> int:
        del script, nkeys
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


async def test_redis_lock_releases_only_its_owner() -> None:
    lock = BeatLock(RedisLockStore("redis://unused", client=_LockRedis()))
    first = await lock.acquire("beat:scan_dead_letter", 30)
    assert first.status is LockStatus.ACQUIRED
    assert first.token is not None
    assert (await lock.acquire("beat:scan_dead_letter", 30)).status is LockStatus.HELD
    await lock.release("beat:scan_dead_letter", "other")
    assert (await lock.acquire("beat:scan_dead_letter", 30)).status is LockStatus.HELD
    await lock.release("beat:scan_dead_letter", first.token)
    assert (await lock.acquire("beat:scan_dead_letter", 30)).status is LockStatus.ACQUIRED


def test_celery_acks_late_and_declares_dlq() -> None:
    from app.tasks.celery_app import celery_app

    names = {queue.name for queue in celery_app.conf.task_queues}
    assert {"high", "sync", "sync.amazon", "batch", "report", "dlq"} <= names
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True


async def test_test_env_factories_do_not_need_redis() -> None:
    from app.adapters.quotas import quota_for
    from app.sync_engine.idempotency import DedupHint, dedup_hint, order_idempotency_key, remember
    from app.sync_engine.runtime import (
        get_bloom,
        get_circuit_breaker,
        get_dead_letter_queue,
        get_rate_limiter,
        reset_runtime,
    )

    reset_runtime()
    try:
        assert (await get_rate_limiter().try_acquire("shopee", "1", quota_for("shopee"))).allowed
        assert await get_circuit_breaker().allow("lazada") is True
        key = order_idempotency_key("tiktok", 3, "Z")
        assert await dedup_hint(get_bloom(), key) is DedupHint.PROBABLY_NEW
        await remember(get_bloom(), key)
        assert await dedup_hint(get_bloom(), key) is DedupHint.PROBABLY_SEEN
        assert await get_dead_letter_queue().size() == 0
    finally:
        reset_runtime()
