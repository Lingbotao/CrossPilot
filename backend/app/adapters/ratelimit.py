"""Redis 令牌桶（M2-01）。

每个「平台 × 店铺」一把桶，键为 ``rl:{platform}:{shop_id}``。
配额来自调用方传入的 ``RateLimitSpec``（``quotas.py``，或 ``platform_rate_limit`` 表覆盖后的结果），本文件不写 QPS。
维度为 ``app`` / ``per_app`` 时，同一平台的店铺共用一把桶。

连续收到 429 时把有效 QPS 乘以降速比例，到底线后不再降；连续成功达到阈值后再升一档。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.adapters.base import RateLimitSpec
from app.core.logging import get_logger
from app.sync_engine.errors import StoreUnavailable
from app.sync_engine.policy import SyncPolicy
from app.sync_engine.state_store import JsonStateStore

log = get_logger(__name__)

_APP_DIMENSIONS = frozenset({"app", "per_app"})


@dataclass(frozen=True, slots=True)
class BucketState:
    tokens: float
    updated_at: datetime
    penalty: float = 1.0
    consecutive_429: int = 0
    consecutive_ok: int = 0


@dataclass(frozen=True, slots=True)
class LimitDecision:
    allowed: bool
    retry_after_seconds: float
    penalty: float


def bucket_key(platform: str, shop_id: str, spec: RateLimitSpec) -> str:
    scope = "app" if spec.dimension in _APP_DIMENSIONS else shop_id
    return f"rl:{platform.strip().lower()}:{scope}"


@dataclass(frozen=True, slots=True)
class DayCount:
    count: int


def day_key(platform: str, shop_id: str, spec: RateLimitSpec, now: datetime) -> str:
    current = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return f"{bucket_key(platform, shop_id, spec)}:day:{current:%Y%m%d}"


def seconds_until_next_utc_day(now: datetime) -> float:
    current = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    nxt = datetime(current.year, current.month, current.day, tzinfo=UTC) + timedelta(days=1)
    return max(1.0, (nxt - current).total_seconds())


def encode_day(state: DayCount) -> str:
    return json.dumps({"count": state.count}, separators=(",", ":"))


def decode_day(raw: str) -> DayCount:
    data = json.loads(raw)
    if not isinstance(data, dict) or "count" not in data:
        raise ValueError("日配额计数损坏")
    return DayCount(count=int(data["count"]))


def _capacity(burst: int, penalty: float) -> float:
    return max(1.0, burst * penalty)


def _fresh(now: datetime, burst: int, penalty: float = 1.0) -> BucketState:
    return BucketState(tokens=_capacity(burst, penalty), updated_at=now, penalty=penalty)


def apply_acquire(
    state: BucketState | None,
    *,
    now: datetime,
    qps: float,
    burst: int,
) -> tuple[bool, float, BucketState]:
    """按当前倍率补充令牌并尝试取走 1 个。返回是否放行、需等待的秒数、新状态。"""
    penalty = 1.0 if state is None else state.penalty
    rate = qps * penalty
    capacity = _capacity(burst, penalty)
    if state is None:
        tokens = capacity
        streak_429 = 0
        streak_ok = 0
    else:
        elapsed = (now - state.updated_at).total_seconds()
        tokens = min(capacity, state.tokens + max(0.0, elapsed) * rate)
        streak_429 = state.consecutive_429
        streak_ok = state.consecutive_ok
    if rate <= 0 or tokens < 1:
        wait = 1.0 if rate <= 0 else (1.0 - tokens) / rate
        denied = BucketState(
            tokens=max(0.0, tokens),
            updated_at=now,
            penalty=penalty,
            consecutive_429=streak_429,
            consecutive_ok=streak_ok,
        )
        return False, wait, denied
    granted = BucketState(
        tokens=tokens - 1,
        updated_at=now,
        penalty=penalty,
        consecutive_429=streak_429,
        consecutive_ok=streak_ok,
    )
    return True, 0.0, granted


def apply_throttle(state: BucketState | None, *, now: datetime, burst: int, policy: SyncPolicy) -> BucketState:
    """连续 429：有效 QPS 乘以降速比例，并截断已经攒下的突发。"""
    base = state if state is not None else _fresh(now, burst)
    penalty = max(policy.penalty_floor, base.penalty * policy.slowdown_ratio)
    return BucketState(
        tokens=min(base.tokens, _capacity(burst, penalty)),
        updated_at=base.updated_at,
        penalty=penalty,
        consecutive_429=base.consecutive_429 + 1,
        consecutive_ok=0,
    )


def apply_success(state: BucketState | None, *, now: datetime, burst: int, policy: SyncPolicy) -> BucketState:
    """成功调用清掉 429 连续计数。安静一段时间后把倍率升回一档。"""
    base = state if state is not None else _fresh(now, burst)
    streak_ok = base.consecutive_ok + 1
    penalty = base.penalty
    if streak_ok >= policy.recover_after_successes and penalty < 1:
        penalty = min(1.0, penalty / policy.slowdown_ratio)
        streak_ok = 0
    return BucketState(
        tokens=min(base.tokens, _capacity(burst, penalty)),
        updated_at=base.updated_at,
        penalty=penalty,
        consecutive_429=0,
        consecutive_ok=streak_ok,
    )


def encode_bucket(state: BucketState) -> str:
    payload = {
        "tokens": state.tokens,
        "updated_at": state.updated_at.astimezone(UTC).isoformat(),
        "penalty": state.penalty,
        "consecutive_429": state.consecutive_429,
        "consecutive_ok": state.consecutive_ok,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def decode_bucket(raw: str) -> BucketState:
    data = json.loads(raw)
    updated = datetime.fromisoformat(str(data["updated_at"]))
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return BucketState(
        tokens=float(data["tokens"]),
        updated_at=updated.astimezone(UTC),
        penalty=float(data.get("penalty", 1)),
        consecutive_429=int(data.get("consecutive_429", 0)),
        consecutive_ok=int(data.get("consecutive_ok", 0)),
    )


class TokenBucketLimiter:
    def __init__(
        self,
        store: JsonStateStore,
        policy: SyncPolicy,
        *,
        clock: Callable[[], datetime] | None = None,
        day_store: JsonStateStore | None = None,
    ) -> None:
        self._store = store
        self._policy = policy
        self._clock = clock or (lambda: datetime.now(UTC))
        self._day_store = day_store

    async def try_acquire(self, platform: str, shop_id: str, spec: RateLimitSpec) -> LimitDecision:
        if spec.daily_quota is not None:
            blocked = await self._daily_block(platform, shop_id, spec)
            if blocked is not None:
                return blocked
        decision = await self._take_token(platform, shop_id, spec)
        if decision.allowed and spec.daily_quota is not None:
            try:
                await self._count_daily(platform, shop_id, spec)
            except StoreUnavailable:
                log.warning("daily_quota_store_unavailable", platform=platform, shop_id=shop_id)
                return LimitDecision(allowed=False, retry_after_seconds=1.0, penalty=decision.penalty)
        return decision

    async def _take_token(self, platform: str, shop_id: str, spec: RateLimitSpec) -> LimitDecision:
        key = bucket_key(platform, shop_id, spec)
        holder: list[LimitDecision] = []

        def mutate(state: BucketState | None) -> BucketState:
            allowed, wait, new_state = apply_acquire(state, now=self._clock(), qps=float(spec.qps), burst=spec.burst)
            holder.append(LimitDecision(allowed=allowed, retry_after_seconds=wait, penalty=new_state.penalty))
            return new_state

        try:
            await self._store.update(key, mutate)
        except StoreUnavailable:
            log.warning("rate_limit_store_unavailable", platform=platform, shop_id=shop_id)
            return LimitDecision(allowed=False, retry_after_seconds=1.0, penalty=1.0)
        return holder[-1]

    async def _daily_block(self, platform: str, shop_id: str, spec: RateLimitSpec) -> LimitDecision | None:
        quota = spec.daily_quota
        if quota is None:
            return None
        if self._day_store is None:
            log.warning("daily_quota_store_missing", platform=platform, shop_id=shop_id)
            return LimitDecision(allowed=False, retry_after_seconds=1.0, penalty=1.0)
        try:
            current = await self._day_store.read(day_key(platform, shop_id, spec, self._clock()))
        except StoreUnavailable:
            log.warning("daily_quota_store_unavailable", platform=platform, shop_id=shop_id)
            return LimitDecision(allowed=False, retry_after_seconds=1.0, penalty=1.0)
        used = 0 if current is None or current.count < 0 else current.count
        if used >= quota:
            return LimitDecision(
                allowed=False,
                retry_after_seconds=seconds_until_next_utc_day(self._clock()),
                penalty=1.0,
            )
        return None

    async def _count_daily(self, platform: str, shop_id: str, spec: RateLimitSpec) -> None:
        if self._day_store is None:
            raise StoreUnavailable("日配额计数器未装配")
        key = day_key(platform, shop_id, spec, self._clock())

        def mutate(state: DayCount | None) -> DayCount:
            current = 0 if state is None or state.count < 0 else state.count
            return DayCount(count=current + 1)

        await self._day_store.update(key, mutate)

    async def record_throttled(self, platform: str, shop_id: str, spec: RateLimitSpec) -> float:
        key = bucket_key(platform, shop_id, spec)
        before = 1.0

        def mutate(state: BucketState | None) -> BucketState:
            nonlocal before
            before = 1.0 if state is None else state.penalty
            return apply_throttle(state, now=self._clock(), burst=spec.burst, policy=self._policy)

        new_state = await self._store.update(key, mutate)
        if new_state.penalty < before:
            log.info(
                "rate_limit_slowdown",
                platform=platform.strip().lower(),
                shop_id=shop_id,
                penalty=new_state.penalty,
            )
        return float(new_state.penalty)

    async def record_success(self, platform: str, shop_id: str, spec: RateLimitSpec) -> float:
        key = bucket_key(platform, shop_id, spec)

        def mutate(state: BucketState | None) -> BucketState:
            return apply_success(state, now=self._clock(), burst=spec.burst, policy=self._policy)

        new_state = await self._store.update(key, mutate)
        return float(new_state.penalty)


__all__ = [
    "BucketState",
    "DayCount",
    "LimitDecision",
    "TokenBucketLimiter",
    "apply_acquire",
    "apply_success",
    "apply_throttle",
    "bucket_key",
    "day_key",
    "decode_bucket",
    "decode_day",
    "encode_bucket",
    "encode_day",
    "seconds_until_next_utc_day",
]
