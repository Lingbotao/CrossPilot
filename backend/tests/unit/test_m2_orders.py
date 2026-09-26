"""M2-05 订单游标、重叠窗口和平台分页。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.adapters.amazon.adapter import AmazonAdapter
from app.adapters.base import CredentialView, PageResult, UnifiedOrder
from app.adapters.shopee.adapter import ShopeeAdapter
from app.models.order import SalesOrder
from app.sync_engine.cursor import (
    checkpoint_for,
    is_order_sync_due,
    order_sync_window,
    pull_order_pages,
    shop_sync_message,
)
from app.sync_engine.queues import task_queue


def test_overlap_rewinds_the_cursor_and_first_sync_looks_back() -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    cursor = datetime(2026, 3, 1, 11, 0, tzinfo=UTC)
    since, until = order_sync_window(
        cursor_at=cursor,
        now=now,
        overlap=timedelta(minutes=5),
        initial_lookback=timedelta(days=1),
    )
    assert until == now
    assert since == cursor - timedelta(minutes=5)
    fresh_since, _fresh_until = order_sync_window(
        cursor_at=None,
        now=now,
        overlap=timedelta(minutes=5),
        initial_lookback=timedelta(days=1),
    )
    assert fresh_since == now - timedelta(days=1)
    clamped, _clamped_until = order_sync_window(
        cursor_at=now + timedelta(hours=2),
        now=now,
        overlap=timedelta(0),
        initial_lookback=timedelta(days=1),
    )
    assert clamped == now - timedelta(seconds=1)


def test_naive_cursor_is_treated_as_utc() -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    since, _until = order_sync_window(
        cursor_at=datetime(2026, 3, 1, 11, 0),
        now=now,
        overlap=timedelta(minutes=5),
        initial_lookback=timedelta(hours=1),
    )
    assert since == datetime(2026, 3, 1, 10, 55, tzinfo=UTC)


def test_shop_is_due_when_never_synced_resuming_or_interval_elapsed() -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    interval = timedelta(seconds=60)
    assert is_order_sync_due(cursor_at=None, resume_cursor=None, now=now, interval=interval)
    assert is_order_sync_due(cursor_at=now, resume_cursor="page-2", now=now, interval=interval)
    assert not is_order_sync_due(cursor_at=now - timedelta(seconds=30), resume_cursor=None, now=now, interval=interval)
    assert is_order_sync_due(cursor_at=now - timedelta(seconds=60), resume_cursor=None, now=now, interval=interval)


def test_checkpoint_advances_only_after_a_clean_pull() -> None:
    since = datetime(2026, 3, 1, 11, 0, tzinfo=UTC)
    until = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    done = checkpoint_for(complete=True, errored=False, failed=0, until=until, resume_cursor=None, since=since)
    assert done is not None
    assert done.cursor_at == until
    assert done.resume_cursor is None
    paused = checkpoint_for(complete=False, errored=False, failed=0, until=until, resume_cursor="p2", since=since)
    assert paused is not None
    assert paused.cursor_at is None
    assert paused.resume_cursor == "p2"
    assert paused.resume_since == since
    assert checkpoint_for(complete=False, errored=True, failed=0, until=until, resume_cursor="p2", since=since) is None
    assert checkpoint_for(complete=True, errored=False, failed=1, until=until, resume_cursor=None, since=since) is None
    assert checkpoint_for(complete=False, errored=False, failed=0, until=until, resume_cursor=None, since=since) is None


def test_due_shop_message_keeps_platform_for_the_queue() -> None:
    payload = shop_sync_message(shop_id=9, tenant_id=3, platform="lazada", trigger="beat")
    assert payload["tenant_id"] == 3
    assert task_queue("sync.shop_orders", platform=str(payload["platform"])) == "sync.lazada"


def test_sales_order_is_not_soft_deleted() -> None:
    assert "deleted_at" not in SalesOrder.__table__.c


def test_pull_stops_at_max_pages_and_on_a_repeated_cursor() -> None:
    async def _run() -> None:
        calls: list[str | None] = []

        async def fetch(cursor: str | None) -> PageResult[UnifiedOrder]:
            calls.append(cursor)
            if cursor is None:
                return PageResult(items=[_order("a")], next_cursor="p2")
            if cursor == "p2":
                return PageResult(items=[_order("b")], next_cursor="p3")
            return PageResult(items=[_order("c")], next_cursor=None)

        paused = await pull_order_pages(fetch, start_cursor=None, max_pages=1)
        assert paused.complete is False
        assert paused.resume_cursor == "p2"
        assert [item.platform_order_id for item in paused.orders] == ["a"]

        async def stuck(cursor: str | None) -> PageResult[UnifiedOrder]:
            del cursor
            return PageResult(items=[_order("a")], next_cursor="same")

        repeated = await pull_order_pages(stuck, start_cursor="same", max_pages=5)
        assert repeated.complete is False
        assert repeated.resume_cursor is None
        assert repeated.pages == 1

        async def done(cursor: str | None) -> PageResult[UnifiedOrder]:
            del cursor
            return PageResult(items=[_order("z")], next_cursor=None)

        finished = await pull_order_pages(done, start_cursor=None, max_pages=3)
        assert finished.complete is True
        assert finished.orders[0].platform_order_id == "z"

        async def boom(cursor: str | None) -> PageResult[UnifiedOrder]:
            del cursor
            raise RuntimeError("platform down")

        failed = await pull_order_pages(boom, start_cursor=None, max_pages=3)
        assert failed.error is not None
        assert failed.pages == 0
        with pytest.raises(ValueError):
            await pull_order_pages(done, start_cursor=None, max_pages=0)

    asyncio.run(_run())


def test_amazon_and_shopee_follow_the_page_cursor() -> None:
    async def _run() -> None:
        amazon_transport = _Script(
            [
                {"payload": {"Orders": [_amazon_raw("A-1")], "NextToken": "next-1"}},
                {"payload": {"Orders": [_amazon_raw("A-2")]}},
            ]
        )
        amazon = AmazonAdapter(amazon_transport)
        cred = _cred("amazon")
        now = datetime(2026, 3, 1, tzinfo=UTC)
        first = await amazon.fetch_orders(cred, since=now, until=now)
        second = await amazon.fetch_orders(cred, since=now, until=now, cursor=first.next_cursor)
        assert first.next_cursor == "next-1"
        assert [item.platform_order_id for item in first.items] == ["A-1"]
        assert second.next_cursor is None
        assert [item.platform_order_id for item in second.items] == ["A-2"]
        assert "NextToken=next-1" in amazon_transport.urls[1]

        shopee_transport = _Script(
            [
                {"response": {"order_list": [_shopee_raw("S-1")], "next_cursor": "c2"}},
                {"response": {"order_list": [_shopee_raw("S-2")]}},
            ]
        )
        shopee = ShopeeAdapter(shopee_transport)
        page = await shopee.fetch_orders(_cred("shopee"), since=now, until=now)
        assert page.next_cursor == "c2"
        assert page.items[0].total_amount == Decimal("19.900000")
        await shopee.fetch_orders(_cred("shopee"), since=now, until=now, cursor="c2")
        assert "cursor=c2" in shopee_transport.urls[1]

    asyncio.run(_run())


class _Script:
    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, object] | None = None,
        platform: str,
    ) -> tuple[int, dict[str, object]]:
        del method, params, json_body, platform
        self.urls.append(url)
        raw = self.pages[len(self.urls) - 1]
        return 200, raw


def _cred(platform: str) -> CredentialView:
    return CredentialView(
        shop_id="1",
        platform=platform,
        site_code="SG",
        access_token="token",
        refresh_token=None,
        expires_at=datetime(2026, 4, 1, tzinfo=UTC),
        extra={},
    )


def _order(order_id: str) -> UnifiedOrder:
    return UnifiedOrder(
        platform="shopee",
        shop_id="1",
        platform_order_id=order_id,
        platform_status="READY_TO_SHIP",
        unified_status="TO_SHIP",
        buyer_name=None,
        buyer_country=None,
        currency="SGD",
        total_amount=Decimal("1.000000"),
        items=[],
        paid_at=None,
        updated_at=datetime(2026, 3, 1, tzinfo=UTC),
        raw={},
    )


def _amazon_raw(order_id: str) -> dict[str, object]:
    return {
        "AmazonOrderId": order_id,
        "OrderStatus": "Unshipped",
        "OrderTotal": {"CurrencyCode": "USD", "Amount": "10.000000"},
        "PurchaseDate": "2026-03-01T00:00:00Z",
        "LastUpdateDate": "2026-03-01T01:00:00Z",
    }


def _shopee_raw(order_id: str) -> dict[str, object]:
    return {
        "order_sn": order_id,
        "order_status": "READY_TO_SHIP",
        "currency": "SGD",
        "total_amount": "19.900000",
        "update_time": 1768467600,
    }
