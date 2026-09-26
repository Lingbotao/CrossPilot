"""同步引擎策略。

税率、配额、退避和熔断窗口都从这里读。业务代码不写 0.5、300、1/4/16 这类字面量。
平台 QPS 不在本文件里，仍由 ``adapters/quotas.py``（以及以后的 ``platform_rate_limit`` 表）提供。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings


def _parse_backoff(raw: str) -> tuple[int, ...]:
    parts = tuple(int(piece.strip()) for piece in raw.split(",") if piece.strip())
    if not parts:
        raise ValueError("SYNC_BACKOFF_SECONDS 不能为空")
    return parts


@dataclass(frozen=True, slots=True)
class SyncPolicy:
    """M2 同步引擎的可调参数。默认值对齐 PRD 8.5 / 8.6。"""

    slowdown_ratio: float = 0.5
    penalty_floor: float = 0.25
    recover_after_successes: int = 20
    backoff_seconds: tuple[int, ...] = (1, 4, 16)
    jitter_ratio: float = 0.2
    circuit_failure_ratio: float = 0.5
    circuit_min_samples: int = 4
    circuit_window: int = 20
    circuit_open_seconds: int = 300
    bloom_bit_size: int = 1_048_576
    bloom_hash_count: int = 7
    beat_lock_ttl_seconds: int = 240
    state_ttl_seconds: int = 3600
    order_overlap_seconds: int = 300
    order_initial_lookback_seconds: int = 86400
    order_interval_seconds: int = 60
    order_max_pages: int = 20

    def __post_init__(self) -> None:
        if not 0 < self.slowdown_ratio <= 1:
            raise ValueError("slowdown_ratio 必须在 (0, 1] 内")
        if not 0 < self.penalty_floor <= 1:
            raise ValueError("penalty_floor 必须在 (0, 1] 内")
        if self.recover_after_successes < 1:
            raise ValueError("recover_after_successes 至少为 1")
        if not self.backoff_seconds or any(step <= 0 for step in self.backoff_seconds):
            raise ValueError("backoff_seconds 的每一档都必须是正整数")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio 必须在 [0, 1] 内")
        if not 0 < self.circuit_failure_ratio < 1:
            raise ValueError("circuit_failure_ratio 必须在 (0, 1) 内")
        if self.circuit_min_samples < 1:
            raise ValueError("circuit_min_samples 至少为 1")
        if self.circuit_window < self.circuit_min_samples:
            raise ValueError("circuit_window 不能小于最小样本数")
        if self.circuit_open_seconds < 1:
            raise ValueError("circuit_open_seconds 至少为 1 秒")
        if self.bloom_hash_count < 1 or self.bloom_bit_size < self.bloom_hash_count:
            raise ValueError("布隆过滤器的位数必须不小于哈希次数")
        if self.beat_lock_ttl_seconds < 1 or self.state_ttl_seconds < 1:
            raise ValueError("锁和状态的 TTL 至少为 1 秒")
        if self.order_overlap_seconds < 0:
            raise ValueError("order_overlap_seconds 不能为负")
        if self.order_initial_lookback_seconds < 1:
            raise ValueError("order_initial_lookback_seconds 至少为 1 秒")
        if self.order_interval_seconds < 1:
            raise ValueError("order_interval_seconds 至少为 1 秒")
        if self.order_max_pages < 1:
            raise ValueError("order_max_pages 至少为 1")


def policy_from_settings(source: Settings | None = None) -> SyncPolicy:
    """用当前配置构造策略。传入的 ``source`` 优先于进程内单例。"""
    if source is None:
        from app.core.config import settings

        source = settings
    return SyncPolicy(
        slowdown_ratio=source.sync_slowdown_ratio,
        penalty_floor=source.sync_penalty_floor,
        recover_after_successes=source.sync_recover_after_successes,
        backoff_seconds=_parse_backoff(source.sync_backoff_seconds),
        jitter_ratio=source.sync_jitter_ratio,
        circuit_failure_ratio=source.sync_circuit_failure_ratio,
        circuit_min_samples=source.sync_circuit_min_samples,
        circuit_window=source.sync_circuit_window,
        circuit_open_seconds=source.sync_circuit_open_seconds,
        bloom_bit_size=source.sync_bloom_bit_size,
        bloom_hash_count=source.sync_bloom_hash_count,
        beat_lock_ttl_seconds=source.sync_beat_lock_ttl_seconds,
        state_ttl_seconds=source.sync_state_ttl_seconds,
        order_overlap_seconds=source.sync_order_overlap_seconds,
        order_initial_lookback_seconds=source.sync_order_initial_lookback_seconds,
        order_interval_seconds=source.sync_order_interval_seconds,
        order_max_pages=source.sync_order_max_pages,
    )


__all__ = ["SyncPolicy", "policy_from_settings"]
