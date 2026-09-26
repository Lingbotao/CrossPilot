"""重试、熔断和死信（M2-04）。

429 走限流器的自适应降速，不计入这里的重试次数，也不打开熔断器。
5xx、超时和连接错误按 1s / 4s / 16s 加抖动重试，用尽后进入死信，原始参数原样保留。
熔断按平台计算：失败率超过阈值后打开固定时间，其他平台不受影响。
"""

from __future__ import annotations

import json
import random
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from app.adapters.errors import RetryDecision
from app.core.logging import get_logger
from app.sync_engine.errors import StoreUnavailable
from app.sync_engine.policy import SyncPolicy
from app.sync_engine.state_store import JsonStateStore

log = get_logger(__name__)

_DLQ_KEY = "dlq:records"


class RetryAction(StrEnum):
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"
    FAIL = "fail"
    REFRESH = "refresh"


@dataclass(frozen=True, slots=True)
class RetryPlan:
    action: RetryAction
    delay_seconds: float
    retry_attempt: int
    refresh_attempted: bool


def counts_as_outage(decision: RetryDecision) -> bool:
    """只有平台故障（5xx / 超时 / 断连）才进入熔断统计。"""
    return decision is RetryDecision.RETRY


def _delay(step: int, policy: SyncPolicy, rng: random.Random) -> float:
    spread = step * policy.jitter_ratio
    return step + rng.uniform(-spread, spread)


def plan_retry(
    decision: RetryDecision,
    *,
    retry_attempt: int,
    refresh_attempted: bool,
    policy: SyncPolicy,
    retry_after_seconds: float | None = None,
    rng: random.Random | None = None,
) -> RetryPlan:
    """根据适配器的重试决策给出下一次动作。``retry_attempt`` 是已经做过的退避次数。"""
    generator = rng if rng is not None else random.Random()
    if decision is RetryDecision.DEAD_LETTER:
        return RetryPlan(RetryAction.DEAD_LETTER, 0.0, retry_attempt, refresh_attempted)
    if decision is RetryDecision.FAIL_FAST:
        return RetryPlan(RetryAction.FAIL, 0.0, retry_attempt, refresh_attempted)
    if decision is RetryDecision.RETRY_AFTER:
        if retry_after_seconds is None or retry_after_seconds < 0:
            delay = float(policy.backoff_seconds[0])
        else:
            delay = float(retry_after_seconds)
        return RetryPlan(RetryAction.RETRY, delay, retry_attempt, refresh_attempted)
    if decision is RetryDecision.REFRESH_TOKEN:
        if refresh_attempted:
            return RetryPlan(RetryAction.FAIL, 0.0, retry_attempt, True)
        return RetryPlan(RetryAction.REFRESH, 0.0, retry_attempt, True)
    if retry_attempt >= len(policy.backoff_seconds):
        return RetryPlan(RetryAction.DEAD_LETTER, 0.0, retry_attempt, refresh_attempted)
    delay = _delay(policy.backoff_seconds[retry_attempt], policy, generator)
    return RetryPlan(RetryAction.RETRY, delay, retry_attempt + 1, refresh_attempted)


class CircuitPhase(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True, slots=True)
class CircuitState:
    phase: CircuitPhase = CircuitPhase.CLOSED
    outcomes: tuple[bool, ...] = ()
    opened_at: datetime | None = None
    probe_in_flight: bool = False


def admit(state: CircuitState, now: datetime, policy: SyncPolicy) -> tuple[CircuitState, bool]:
    if state.phase is CircuitPhase.CLOSED:
        return state, True
    if state.phase is CircuitPhase.OPEN:
        if state.opened_at is not None and (now - state.opened_at).total_seconds() >= policy.circuit_open_seconds:
            probing = CircuitState(
                phase=CircuitPhase.HALF_OPEN,
                outcomes=state.outcomes,
                opened_at=state.opened_at,
                probe_in_flight=True,
            )
            return probing, True
        return state, False
    if state.probe_in_flight:
        return state, False
    return CircuitState(
        phase=CircuitPhase.HALF_OPEN,
        outcomes=state.outcomes,
        opened_at=state.opened_at,
        probe_in_flight=True,
    ), True


def observe(state: CircuitState, *, failed: bool, now: datetime, policy: SyncPolicy) -> CircuitState:
    if state.phase is CircuitPhase.HALF_OPEN:
        if failed:
            return CircuitState(phase=CircuitPhase.OPEN, outcomes=state.outcomes, opened_at=now, probe_in_flight=False)
        return CircuitState()
    if state.phase is CircuitPhase.OPEN:
        return state
    outcomes = (*state.outcomes, failed)[-policy.circuit_window :]
    if len(outcomes) >= policy.circuit_min_samples:
        ratio = sum(1 for item in outcomes if item) / len(outcomes)
        if ratio > policy.circuit_failure_ratio:
            return CircuitState(phase=CircuitPhase.OPEN, outcomes=outcomes, opened_at=now, probe_in_flight=False)
    return CircuitState(phase=CircuitPhase.CLOSED, outcomes=outcomes)


def encode_circuit(state: CircuitState) -> str:
    payload = {
        "phase": str(state.phase),
        "outcomes": [1 if item else 0 for item in state.outcomes],
        "opened_at": None if state.opened_at is None else state.opened_at.astimezone(UTC).isoformat(),
        "probe_in_flight": state.probe_in_flight,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def decode_circuit(raw: str) -> CircuitState:
    data = json.loads(raw)
    opened_raw = data.get("opened_at")
    opened: datetime | None = None
    if opened_raw:
        opened = datetime.fromisoformat(str(opened_raw))
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=UTC)
        opened = opened.astimezone(UTC)
    return CircuitState(
        phase=CircuitPhase(str(data.get("phase", CircuitPhase.CLOSED))),
        outcomes=tuple(bool(item) for item in data.get("outcomes", [])),
        opened_at=opened,
        probe_in_flight=bool(data.get("probe_in_flight", False)),
    )


class CircuitBreaker:
    def __init__(
        self,
        store: JsonStateStore,
        policy: SyncPolicy,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))

    def _key(self, platform: str) -> str:
        return f"cb:{platform.strip().lower()}"

    async def allow(self, platform: str) -> bool:
        holder: list[bool] = []

        def mutate(state: CircuitState | None) -> CircuitState:
            new_state, allowed = admit(state or CircuitState(), self._clock(), self._policy)
            holder.append(allowed)
            return new_state

        await self._store.update(self._key(platform), mutate)
        return holder[-1]

    async def record(self, platform: str, *, failed: bool) -> CircuitPhase:
        holder: list[CircuitPhase] = []

        def mutate(state: CircuitState | None) -> CircuitState:
            current = state or CircuitState()
            new_state = observe(current, failed=failed, now=self._clock(), policy=self._policy)
            holder.append(new_state.phase)
            if new_state.phase is CircuitPhase.OPEN and current.phase is not CircuitPhase.OPEN:
                log.warning(
                    "platform_circuit_open",
                    platform=platform.strip().lower(),
                    open_seconds=self._policy.circuit_open_seconds,
                )
            return new_state

        await self._store.update(self._key(platform), mutate)
        return holder[-1]


@dataclass(frozen=True, slots=True)
class DeadLetter:
    task_name: str
    tenant_id: int
    kwargs: dict[str, Any]
    error: str
    failed_at: str


def make_dead_letter(
    *,
    task_name: str,
    tenant_id: int,
    kwargs: dict[str, Any],
    error: str,
    failed_at: datetime | None = None,
) -> DeadLetter:
    when = failed_at.astimezone(UTC) if failed_at is not None else datetime.now(UTC)
    return DeadLetter(
        task_name=task_name,
        tenant_id=tenant_id,
        kwargs=dict(kwargs),
        error=error,
        failed_at=when.isoformat(),
    )


def dump_dead_letter(item: DeadLetter) -> str:
    payload = {
        "task_name": item.task_name,
        "tenant_id": item.tenant_id,
        "kwargs": item.kwargs,
        "error": item.error,
        "failed_at": item.failed_at,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def load_dead_letter(raw: str) -> DeadLetter:
    data = json.loads(raw)
    kwargs = data["kwargs"]
    if not isinstance(kwargs, dict):
        raise TypeError("dead letter kwargs must be an object")
    return DeadLetter(
        task_name=str(data["task_name"]),
        tenant_id=int(data["tenant_id"]),
        kwargs={str(key): value for key, value in kwargs.items()},
        error=str(data["error"]),
        failed_at=str(data["failed_at"]),
    )


class LetterList(Protocol):
    async def push(self, item: DeadLetter) -> None: ...

    async def pop(self) -> DeadLetter | None: ...

    async def size(self) -> int: ...


class MemoryLetterList:
    def __init__(self) -> None:
        self._items: deque[DeadLetter] = deque()

    async def push(self, item: DeadLetter) -> None:
        self._items.append(item)

    async def pop(self) -> DeadLetter | None:
        if not self._items:
            return None
        return self._items.popleft()

    async def size(self) -> int:
        return len(self._items)


class RedisLetterList:
    def __init__(self, url: str, *, client: Any | None = None, key: str = _DLQ_KEY) -> None:
        self._url = url
        self._client = client
        self._key = key

    async def _redis(self) -> Any:
        if self._client is None:
            from redis.asyncio import Redis

            self._client = Redis.from_url(self._url, decode_responses=True)
        return self._client

    async def push(self, item: DeadLetter) -> None:
        try:
            client = await self._redis()
            await client.rpush(self._key, dump_dead_letter(item))
        except _redis_errors() as exc:
            raise StoreUnavailable(str(exc)) from exc

    async def pop(self) -> DeadLetter | None:
        try:
            client = await self._redis()
            raw = await client.lpop(self._key)
        except _redis_errors() as exc:
            raise StoreUnavailable(str(exc)) from exc
        if raw is None:
            return None
        return load_dead_letter(str(raw))

    async def size(self) -> int:
        try:
            client = await self._redis()
            return int(await client.llen(self._key))
        except _redis_errors() as exc:
            raise StoreUnavailable(str(exc)) from exc


def _redis_errors() -> tuple[type[BaseException], ...]:
    import redis.exceptions

    return (redis.exceptions.RedisError, TimeoutError, OSError)


class DeadLetterQueue:
    def __init__(self, letters: LetterList) -> None:
        self._letters = letters

    async def push(self, item: DeadLetter) -> None:
        await self._letters.push(item)

    async def replay_next(self) -> DeadLetter | None:
        return await self._letters.pop()

    async def size(self) -> int:
        return await self._letters.size()


__all__ = [
    "CircuitBreaker",
    "CircuitPhase",
    "CircuitState",
    "DeadLetter",
    "DeadLetterQueue",
    "MemoryLetterList",
    "RedisLetterList",
    "RetryAction",
    "RetryPlan",
    "admit",
    "counts_as_outage",
    "decode_circuit",
    "dump_dead_letter",
    "encode_circuit",
    "load_dead_letter",
    "make_dead_letter",
    "observe",
    "plan_retry",
]
