"""M2-04 退避、熔断和死信。"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.errors import RetryDecision
from app.sync_engine.policy import SyncPolicy
from app.sync_engine.resilience import (
    CircuitBreaker,
    CircuitPhase,
    DeadLetterQueue,
    MemoryLetterList,
    RedisLetterList,
    RetryAction,
    counts_as_outage,
    decode_circuit,
    dump_dead_letter,
    encode_circuit,
    load_dead_letter,
    make_dead_letter,
    plan_retry,
)
from app.sync_engine.state_store import JsonStateStore, MemoryJsonKv


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _breaker(clock: Clock, policy: SyncPolicy | None = None) -> CircuitBreaker:
    active = policy or SyncPolicy(circuit_min_samples=4, circuit_window=4, circuit_open_seconds=300)
    store = JsonStateStore(MemoryJsonKv(), ttl_seconds=60, encode=encode_circuit, decode=decode_circuit)
    return CircuitBreaker(store, active, clock=clock)


def test_backoff_ladder_then_dead_letter() -> None:
    policy = SyncPolicy(jitter_ratio=0)
    attempt = 0
    delays: list[float] = []
    for _ in range(3):
        plan = plan_retry(RetryDecision.RETRY, retry_attempt=attempt, refresh_attempted=False, policy=policy)
        assert plan.action is RetryAction.RETRY
        delays.append(plan.delay_seconds)
        attempt = plan.retry_attempt
    assert delays == [1, 4, 16]
    exhausted = plan_retry(RetryDecision.RETRY, retry_attempt=attempt, refresh_attempted=False, policy=policy)
    assert exhausted.action is RetryAction.DEAD_LETTER


def test_jitter_stays_inside_the_ratio() -> None:
    policy = SyncPolicy(jitter_ratio=0.2)
    plan = plan_retry(
        RetryDecision.RETRY,
        retry_attempt=0,
        refresh_attempted=False,
        policy=policy,
        rng=random.Random(1),
    )
    assert 0.8 <= plan.delay_seconds <= 1.2


def test_fail_fast_and_dead_letter_do_not_retry() -> None:
    policy = SyncPolicy()
    fail = plan_retry(RetryDecision.FAIL_FAST, retry_attempt=0, refresh_attempted=False, policy=policy)
    assert fail.action is RetryAction.FAIL
    dead = plan_retry(RetryDecision.DEAD_LETTER, retry_attempt=1, refresh_attempted=False, policy=policy)
    assert dead.action is RetryAction.DEAD_LETTER
    fallback = plan_retry(
        RetryDecision.RETRY_AFTER,
        retry_attempt=0,
        refresh_attempted=False,
        policy=policy,
        retry_after_seconds=-1,
    )
    assert fallback.delay_seconds == policy.backoff_seconds[0]
    assert fallback.retry_attempt == 0


def test_429_does_not_consume_retry_budget() -> None:
    policy = SyncPolicy()
    plan = plan_retry(
        RetryDecision.RETRY_AFTER,
        retry_attempt=2,
        refresh_attempted=False,
        policy=policy,
        retry_after_seconds=9,
    )
    assert plan.action is RetryAction.RETRY
    assert plan.delay_seconds == 9
    assert plan.retry_attempt == 2


def test_refresh_is_attempted_once() -> None:
    policy = SyncPolicy()
    first = plan_retry(RetryDecision.REFRESH_TOKEN, retry_attempt=0, refresh_attempted=False, policy=policy)
    assert first.action is RetryAction.REFRESH
    assert first.refresh_attempted is True
    second = plan_retry(RetryDecision.REFRESH_TOKEN, retry_attempt=0, refresh_attempted=True, policy=policy)
    assert second.action is RetryAction.FAIL


@pytest.mark.parametrize(
    ("decision", "outage"),
    [
        (RetryDecision.RETRY, True),
        (RetryDecision.RETRY_AFTER, False),
        (RetryDecision.FAIL_FAST, False),
        (RetryDecision.REFRESH_TOKEN, False),
    ],
)
def test_only_platform_outage_counts_toward_the_breaker(decision: RetryDecision, outage: bool) -> None:
    assert counts_as_outage(decision) is outage


async def test_breaker_opens_above_half_and_leaves_other_platforms_closed() -> None:
    clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
    quiet = _breaker(clock)
    for failed in (True, True, False, False):
        assert await quiet.record("amazon", failed=failed) is CircuitPhase.CLOSED

    breaker = _breaker(clock)
    phase = CircuitPhase.CLOSED
    for failed in (True, True, True, False):
        phase = await breaker.record("amazon", failed=failed)
    assert phase is CircuitPhase.OPEN
    assert await breaker.allow("amazon") is False
    assert await breaker.allow("shopee") is True


async def test_open_circuit_probes_after_the_window() -> None:
    clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
    breaker = _breaker(clock)
    for failed in (True, True, True, False):
        await breaker.record("amazon", failed=failed)
    clock.advance(299)
    assert await breaker.allow("amazon") is False
    clock.advance(1)
    assert await breaker.allow("amazon") is True
    assert await breaker.allow("amazon") is False
    assert await breaker.record("amazon", failed=False) is CircuitPhase.CLOSED
    assert await breaker.allow("amazon") is True


async def test_failed_probe_reopens_the_circuit() -> None:
    clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
    breaker = _breaker(clock)
    for failed in (True, True, True, False):
        await breaker.record("amazon", failed=failed)
    clock.advance(300)
    assert await breaker.allow("amazon") is True
    assert await breaker.record("amazon", failed=True) is CircuitPhase.OPEN
    assert await breaker.allow("amazon") is False


async def test_dead_letter_keeps_original_args_in_order() -> None:
    queue = DeadLetterQueue(MemoryLetterList())
    when = datetime(2026, 1, 1, tzinfo=UTC)
    first = make_dead_letter(
        task_name="sync.shop_orders",
        tenant_id=7,
        kwargs={"shop_id": 3, "tenant_id": 7, "platform": "shopee"},
        error="timeout",
        failed_at=when,
    )
    second = make_dead_letter(
        task_name="sync.shop_orders",
        tenant_id=8,
        kwargs={"shop_id": 4, "tenant_id": 8},
        error="boom",
        failed_at=when,
    )
    await queue.push(first)
    await queue.push(second)
    assert await queue.size() == 2
    replayed = await queue.replay_next()
    assert replayed is not None
    assert replayed.tenant_id == 7
    assert replayed.kwargs["platform"] == "shopee"
    assert replayed.error == "timeout"
    restored = load_dead_letter(dump_dead_letter(replayed))
    assert restored.kwargs == replayed.kwargs
    assert await queue.size() == 1


class _ListRedis:
    def __init__(self) -> None:
        self.items: list[str] = []

    async def rpush(self, key: str, value: str) -> int:
        del key
        self.items.append(value)
        return len(self.items)

    async def lpop(self, key: str) -> str | None:
        del key
        if not self.items:
            return None
        return self.items.pop(0)

    async def llen(self, key: str) -> int:
        del key
        return len(self.items)


async def test_redis_dead_letter_roundtrip() -> None:
    queue = DeadLetterQueue(RedisLetterList("redis://unused", client=_ListRedis()))
    item = make_dead_letter(task_name="sync.shop_orders", tenant_id=1, kwargs={"shop_id": 2}, error="x")
    await queue.push(item)
    replayed = await queue.replay_next()
    assert replayed is not None
    assert replayed.kwargs == {"shop_id": 2}
    assert await queue.replay_next() is None
