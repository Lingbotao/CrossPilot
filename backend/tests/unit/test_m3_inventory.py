"""库存可售、预占、回传批量和滞后降 0。不连数据库。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.adapters.quotas import quota_for
from app.engines.inventory import (
    BinView,
    StockLevel,
    StockOutcome,
    chunk_updates,
    mark_posted,
    plan_allocations,
    platform_push_quantity,
    restock_bucket,
    sellable_qty,
    should_push_zero,
    should_reserve_line,
    simulate_parallel_reserves,
    suggest_replenishment,
    try_release,
    try_reserve,
    try_ship,
)
from app.engines.order_desk import RestockStatus
from app.models import TENANT_SCOPED_TABLES
from app.models.inventory import DEFAULT_SAFE_STOCK


def test_inventory_tables_are_tenant_scoped() -> None:
    for name in (
        "warehouse",
        "inventory",
        "inventory_flow",
        "inventory_hold",
        "platform_safety_stock",
        "inventory_push_log",
    ):
        assert name in TENANT_SCOPED_TABLES


def test_sellable_uses_warehouse_buffer_only() -> None:
    assert sellable_qty(10, 3, DEFAULT_SAFE_STOCK) == 5
    assert sellable_qty(2, 0, DEFAULT_SAFE_STOCK) == 0


def test_platform_push_does_not_stack_warehouse_safety() -> None:
    free = 10
    assert sellable_qty(free, 0, DEFAULT_SAFE_STOCK) == 8
    assert platform_push_quantity(free, 3) == 7
    assert platform_push_quantity(free, 3) != free - DEFAULT_SAFE_STOCK - 3


def test_unmatched_sku_is_not_reserved() -> None:
    assert should_reserve_line(None, 2) is False
    assert should_reserve_line(10, 0) is False
    assert should_reserve_line(10, 2) is True


def test_parallel_reserves_never_go_negative() -> None:
    full_ok, full = simulate_parallel_reserves(100, 100)
    assert full_ok == 100
    assert full.occupied == 100
    assert full.available == 100
    assert full.occupied <= full.available

    short_ok, short = simulate_parallel_reserves(40, 100)
    assert short_ok == 40
    assert short.occupied == 40
    assert short.occupied <= short.available
    assert short.available - short.occupied == 0


def test_stale_version_conflicts_and_release_restores_free_stock() -> None:
    level = StockLevel(available=5, occupied=0, version=0)
    assert try_reserve(level, 2, seen_version=0) is StockOutcome.APPLIED
    assert try_reserve(level, 1, seen_version=0) is StockOutcome.CONFLICT
    assert level.occupied == 2
    assert try_release(level, 2, seen_version=level.version) is StockOutcome.APPLIED
    assert level.occupied == 0
    assert try_ship(StockLevel(available=2, occupied=2, version=1), 2, seen_version=1) is StockOutcome.APPLIED


def test_ship_refuses_to_drive_stock_negative() -> None:
    level = StockLevel(available=1, occupied=1, version=3)
    assert try_ship(level, 2, seen_version=3) is StockOutcome.INSUFFICIENT
    assert level.available == 1
    assert level.occupied == 1


def test_default_warehouse_is_used_before_platform_warehouse() -> None:
    bins = [
        BinView(warehouse_id=1, available=1, occupied=0, is_default=False, warehouse_type="PLATFORM"),
        BinView(warehouse_id=2, available=4, occupied=0, is_default=True, warehouse_type="LOCAL"),
        BinView(warehouse_id=3, available=3, occupied=0, is_default=False, warehouse_type="OVERSEAS"),
    ]
    taken, short = plan_allocations(bins, 6)
    assert short == 0
    assert [(item.warehouse_id, item.quantity) for item in taken] == [(2, 4), (3, 2)]


def test_return_restock_posts_deferred_into_available_or_defective() -> None:
    assert restock_bucket(sellable=True) == "available"
    assert restock_bucket(sellable=False) == "defective"
    assert mark_posted(RestockStatus.DEFERRED.value) == RestockStatus.POSTED.value
    assert mark_posted(RestockStatus.NONE.value) == RestockStatus.NONE.value


def test_push_chunks_follow_platform_batch_limit() -> None:
    shopee_limit = quota_for("shopee").batch_limit
    amazon_limit = quota_for("amazon").batch_limit
    items = list(range(shopee_limit + 1))
    shopee_chunks = chunk_updates(items, shopee_limit)
    assert len(shopee_chunks[0]) == shopee_limit
    assert len(shopee_chunks[1]) == 1
    assert all(len(chunk) <= amazon_limit for chunk in chunk_updates(items, amazon_limit))


def test_lagged_shop_pushes_zero() -> None:
    now = datetime(2026, 10, 9, tzinfo=UTC)
    lag = quota_for("lazada").stock_push_lag_seconds
    assert should_push_zero(
        last_success_at=now - timedelta(seconds=lag + 1),
        anchor=now - timedelta(days=1),
        now=now,
        lag_seconds=lag,
    )
    assert not should_push_zero(
        last_success_at=now - timedelta(seconds=lag - 1),
        anchor=now - timedelta(days=1),
        now=now,
        lag_seconds=lag,
    )


def test_replenishment_uses_lead_time_cover_and_safety() -> None:
    today = datetime(2026, 10, 9, tzinfo=UTC).date()
    suggestion = suggest_replenishment(
        sold=30,
        window_days=30,
        lead_time_days=10,
        cover_days=5,
        safety=2,
        movable=4,
        in_transit=1,
        today=today,
    )
    assert suggestion.suggested_qty == 12
    assert suggestion.order_on == today
