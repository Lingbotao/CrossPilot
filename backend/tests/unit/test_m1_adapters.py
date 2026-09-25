"""M1 适配器契约：签名、授权链接、fixture 订单金额与健康度。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.adapters.base import CredentialView
from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError, RetryDecision
from app.adapters.registry import adapter_registry
from app.adapters.signing import lazada_sign, shopee_sign, tiktok_sign
from app.models.enums import ShopStatus, SyncStatus
from app.services.shop_health import evaluate_shop_health, next_refresh_failure

register_builtin_adapters()


def test_shopee_sign_uses_partner_path_timestamp() -> None:
    sign = shopee_sign(
        partner_id="1000",
        partner_key="partner-key",
        path="/api/v2/shop/auth_partner",
        timestamp=1700000000,
    )
    expected = hmac.new(
        b"partner-key",
        b"1000/api/v2/shop/auth_partner1700000000",
        hashlib.sha256,
    ).hexdigest()
    assert sign == expected
    assert lazada_sign(app_secret="secret", path="/rest/auth/token/create", params={"b": "2", "a": "1"}).isupper()
    assert len(tiktok_sign(app_secret="secret", path="/api/v2/token/get", params={"app_key": "k"})) == 64


def test_four_platforms_exchange_and_parse_decimal_orders() -> None:
    async def _run() -> None:
        cases = (
            ("amazon", "US", "111-0000000-0000001", "USD", "24.500000"),
            ("shopee", "SG", "2601150000001", "SGD", "19.900000"),
            ("lazada", "MY", "9000000000001", "SGD", "15.000000"),
            ("tiktok", "US", "576460000000000001", "USD", "18.000000"),
        )
        now = datetime.now(UTC)
        for platform, site, order_id, currency, amount in cases:
            adapter = adapter_registry.get(platform)
            url = adapter.build_auth_url("https://app.example/cb", "state-token", site_code=site)
            if platform == "amazon":
                assert "sellercentral" in url
            else:
                assert site.lower() in url.lower()
            bundle = await adapter.exchange_token("fixture-code", site_code=site)
            assert bundle.platform_shop_id.endswith(site)
            cred = CredentialView(
                shop_id="1",
                platform=platform,
                site_code=site,
                access_token=bundle.access_token,
                refresh_token=bundle.refresh_token,
                expires_at=bundle.expires_at,
                extra={"platform_shop_id": bundle.platform_shop_id},
            )
            refreshed = await adapter.refresh_token(cred)
            assert refreshed.access_token != bundle.access_token
            assert refreshed.platform_shop_id == bundle.platform_shop_id
            page = await adapter.fetch_orders(cred, since=now - timedelta(days=1), until=now)
            order = page.items[0]
            assert order.platform_order_id == order_id
            assert order.currency == currency
            assert order.unified_status == "TO_SHIP"
            assert isinstance(order.total_amount, Decimal)
            assert f"{order.total_amount:.6f}" == amount
            with_error = adapter.normalize_error(AdapterError("x", platform=platform, decision=RetryDecision.FAIL_FAST))
            assert with_error.decision == RetryDecision.FAIL_FAST

    asyncio.run(_run())


def test_v1_adapter_has_no_reply_message() -> None:
    adapter = adapter_registry.get("shopee")
    assert not hasattr(adapter, "reply_message")


def test_shop_health_and_refresh_alert_threshold() -> None:
    now = datetime(2026, 1, 15, tzinfo=UTC)
    lead = timedelta(minutes=30)
    stale = timedelta(hours=1)
    green, _ = evaluate_shop_health(
        status=int(ShopStatus.ACTIVE),
        refresh_fail_count=0,
        expires_at=now + timedelta(hours=4),
        last_sync_at=now,
        last_sync_status=int(SyncStatus.SUCCESS),
        last_error=None,
        now=now,
        lead=lead,
        stale=stale,
        alert_threshold=3,
    )
    yellow, yellow_reason = evaluate_shop_health(
        status=int(ShopStatus.ACTIVE),
        refresh_fail_count=0,
        expires_at=now + timedelta(hours=4),
        last_sync_at=None,
        last_sync_status=None,
        last_error=None,
        now=now,
        lead=lead,
        stale=stale,
        alert_threshold=3,
    )
    red, _ = evaluate_shop_health(
        status=int(ShopStatus.ACTIVE),
        refresh_fail_count=3,
        expires_at=now + timedelta(hours=4),
        last_sync_at=now,
        last_sync_status=int(SyncStatus.SUCCESS),
        last_error="刷新失败",
        now=now,
        lead=lead,
        stale=stale,
        alert_threshold=3,
    )
    assert green == "green"
    assert yellow == "yellow"
    assert yellow_reason == "尚未同步"
    assert red == "red"
    assert next_refresh_failure(2, 3) == (3, True)
    assert next_refresh_failure(0, 3) == (1, False)
