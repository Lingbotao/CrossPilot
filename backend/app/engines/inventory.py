"""库存可售、预占和回传数量。纯函数，不访问数据库。

分仓可售走 ``available - occupied - safe_stock``。
平台回传走各仓可动用之和减去该平台水位。两套水位不叠减。
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum
from typing import TypeGuard, TypeVar

T = TypeVar("T")


class StockOutcome(StrEnum):
    APPLIED = "applied"
    INSUFFICIENT = "insufficient"
    CONFLICT = "conflict"


@dataclass
class StockLevel:
    """一个仓的良品实物与预占。乐观锁看 ``version``。"""

    available: int
    occupied: int
    version: int


@dataclass(frozen=True, slots=True)
class BinView:
    warehouse_id: int
    available: int
    occupied: int
    is_default: bool
    warehouse_type: str


@dataclass(frozen=True, slots=True)
class Allocation:
    warehouse_id: int
    quantity: int


@dataclass(frozen=True, slots=True)
class Replenishment:
    suggested_qty: int
    order_on: date | None
    daily_sales: Decimal


def should_reserve_line(sku_id: int | None, quantity: int) -> TypeGuard[int]:
    """未匹配 SKU 或数量不是正数时不预占，订单同步继续。"""

    return sku_id is not None and quantity > 0


def sellable_qty(available: int, occupied: int, safe_stock: int) -> int:
    """与 ``v_sellable_inventory`` 同一条公式。"""

    return max(available - occupied - safe_stock, 0)


def free_qty(available: int, occupied: int) -> int:
    """还能预占的件数。安全水位不参与预占，只参与可售和回传。"""

    return available - occupied


def platform_push_quantity(on_hand_free: int, platform_safety: int) -> int:
    """回传量 = 各仓 (available - occupied) 之和 - 平台水位。不再减分仓 safe_stock。"""

    if on_hand_free < 0 or platform_safety < 0:
        raise ValueError("回传数量不能为负")
    return max(on_hand_free - platform_safety, 0)


def plan_allocations(bins: Sequence[BinView], quantity: int) -> tuple[list[Allocation], int]:
    """先扣默认仓，再扣其他非平台仓。返回已分配和仍然缺的数量。"""

    if quantity < 0:
        raise ValueError("预占数量不能为负")
    ranked = sorted(
        bins,
        key=lambda item: (
            0 if item.is_default else 1,
            -(item.available - item.occupied),
            item.warehouse_id,
        ),
    )
    left = quantity
    chosen: list[Allocation] = []
    for item in ranked:
        if left == 0:
            break
        if not item.is_default and item.warehouse_type == "PLATFORM":
            continue
        free = item.available - item.occupied
        if free <= 0:
            continue
        take = min(free, left)
        chosen.append(Allocation(warehouse_id=item.warehouse_id, quantity=take))
        left -= take
    return chosen, left


def try_reserve(level: StockLevel, quantity: int, *, seen_version: int) -> StockOutcome:
    if quantity < 0:
        raise ValueError("预占数量不能为负")
    if level.version != seen_version:
        return StockOutcome.CONFLICT
    if free_qty(level.available, level.occupied) < quantity:
        return StockOutcome.INSUFFICIENT
    level.occupied += quantity
    level.version += 1
    return StockOutcome.APPLIED


def try_release(level: StockLevel, quantity: int, *, seen_version: int) -> StockOutcome:
    if quantity < 0:
        raise ValueError("释放数量不能为负")
    if level.version != seen_version:
        return StockOutcome.CONFLICT
    if level.occupied < quantity:
        return StockOutcome.INSUFFICIENT
    level.occupied -= quantity
    level.version += 1
    return StockOutcome.APPLIED


def try_ship(level: StockLevel, quantity: int, *, seen_version: int) -> StockOutcome:
    """发货：实物和预占一起减。预占不足或实物不足都不改数字。"""

    if quantity < 0:
        raise ValueError("出库数量不能为负")
    if level.version != seen_version:
        return StockOutcome.CONFLICT
    if level.occupied < quantity or level.available < quantity:
        return StockOutcome.INSUFFICIENT
    level.available -= quantity
    level.occupied -= quantity
    level.version += 1
    return StockOutcome.APPLIED


def try_outbound(level: StockLevel, quantity: int, *, seen_version: int) -> StockOutcome:
    """没预占过的出库只减实物，且不能吃掉已预占的部分。"""

    if quantity < 0:
        raise ValueError("出库数量不能为负")
    if level.version != seen_version:
        return StockOutcome.CONFLICT
    if free_qty(level.available, level.occupied) < quantity:
        return StockOutcome.INSUFFICIENT
    level.available -= quantity
    level.version += 1
    return StockOutcome.APPLIED


def try_inbound(level: StockLevel, quantity: int, *, seen_version: int) -> StockOutcome:
    if quantity < 0:
        raise ValueError("入库数量不能为负")
    if level.version != seen_version:
        return StockOutcome.CONFLICT
    level.available += quantity
    level.version += 1
    return StockOutcome.APPLIED


def chunk_updates(items: Sequence[T], batch_limit: int) -> list[list[T]]:
    """按平台配置的批量上限切片。上限来自调用方，这里不写死 50。"""

    if batch_limit < 1:
        raise ValueError("批量上限必须大于 0")
    return [list(items[index : index + batch_limit]) for index in range(0, len(items), batch_limit)]


def should_push_zero(
    *,
    last_success_at: datetime | None,
    anchor: datetime,
    now: datetime,
    lag_seconds: int,
) -> bool:
    """最近一次成功回传（或从未回传时的锚点）超过滞后阈值，就改回传 0。"""

    if lag_seconds < 1:
        raise ValueError("滞后阈值必须大于 0")
    basis = anchor if last_success_at is None else last_success_at
    if basis.tzinfo is None or now.tzinfo is None:
        raise ValueError("时间必须带时区")
    return (now - basis).total_seconds() > lag_seconds


def suggest_replenishment(
    *,
    sold: int,
    window_days: int,
    lead_time_days: int,
    cover_days: int,
    safety: int,
    movable: int,
    in_transit: int,
    today: date,
) -> Replenishment:
    """建议量 = 日均 × (交期 + 覆盖天数) + 平台水位 - 可动用 - 在途。"""

    if window_days < 1:
        raise ValueError("统计窗口至少 1 天")
    if min(sold, lead_time_days, cover_days, safety, movable, in_transit) < 0:
        raise ValueError("补货参数不能为负")
    daily = Decimal(sold) / Decimal(window_days)
    raw = daily * Decimal(lead_time_days + cover_days) + Decimal(safety) - Decimal(movable) - Decimal(in_transit)
    suggested = 0 if raw <= 0 else int(raw.to_integral_value(rounding=ROUND_CEILING))
    if daily == 0:
        return Replenishment(suggested_qty=suggested, order_on=today if suggested > 0 else None, daily_sales=daily)
    cover = Decimal(movable) / daily
    if cover < Decimal(lead_time_days):
        return Replenishment(suggested_qty=suggested, order_on=today, daily_sales=daily)
    extra_days = int((cover - Decimal(lead_time_days)).to_integral_value(rounding=ROUND_FLOOR))
    return Replenishment(
        suggested_qty=suggested,
        order_on=today + timedelta(days=extra_days),
        daily_sales=daily,
    )


def restock_bucket(*, sellable: bool) -> str:
    """退货入库：计入可售则加实物，否则加残次。"""

    return "available" if sellable else "defective"


def mark_posted(status: str) -> str:
    """只有待入库的退货单才会变成已入账。"""

    if status != "DEFERRED":
        return status
    return "POSTED"


def simulate_parallel_reserves(available: int, attempts: int, *, quantity: int = 1) -> tuple[int, StockLevel]:
    """用乐观锁规则模拟多笔预占。成功笔数不会超过实物能接住的件数。"""

    level = StockLevel(available=available, occupied=0, version=0)
    guard = threading.Lock()
    successes = 0

    def attempt() -> None:
        nonlocal successes
        for _ in range(16):
            with guard:
                outcome = try_reserve(level, quantity, seen_version=level.version)
            if outcome is StockOutcome.APPLIED:
                successes += 1
                return
            if outcome is StockOutcome.INSUFFICIENT:
                return

    workers = min(32, max(attempts, 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda _: attempt(), range(attempts)))
    return successes, level


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "Allocation",
    "BinView",
    "Replenishment",
    "StockLevel",
    "StockOutcome",
    "chunk_updates",
    "ensure_utc",
    "free_qty",
    "mark_posted",
    "plan_allocations",
    "platform_push_quantity",
    "restock_bucket",
    "sellable_qty",
    "should_push_zero",
    "should_reserve_line",
    "simulate_parallel_reserves",
    "suggest_replenishment",
    "try_inbound",
    "try_outbound",
    "try_release",
    "try_reserve",
    "try_ship",
]
