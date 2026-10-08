"""TikTok Shop Partner 授权。"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import quote

from app.adapters.base import (
    BatchResult,
    CredentialView,
    PageResult,
    PlatformAdapter,
    PriceUpdate,
    PublishResult,
    RateLimitSpec,
    RemoteListing,
    TokenBundle,
    UnifiedOrder,
    UnifiedProduct,
    WebhookEvent,
    WebhookKind,
)
from app.adapters.catalog import fetch_remote_listing, publish_listing, update_listing_prices
from app.adapters.credentials import app_credentials
from app.adapters.oauth_parse import tiktok_order, token_bundle
from app.adapters.quotas import quota_for
from app.adapters.shipping import post_order_change, post_shipment
from app.adapters.signing import signatures_match, tiktok_push_sign, tiktok_sign
from app.adapters.transport import PlatformTransport, default_transport
from app.adapters.webhook_common import coalesce_id, header_value, load_object, matching_order, nested_dict, unix_time

_AUTH_HOST = "https://auth.tiktok-shops.com"
_API_HOST = "https://open-api.tiktokglobalshop.com"
_TOKEN_PATH = "/api/v2/token/get"
_REFRESH_PATH = "/api/v2/token/refresh"
_ORDER_PATH = "/order/202309/orders/search"
_ORDER_DETAIL_PATH = "/order/202309/orders"
_PUSH_ORDER = 1


class TikTokAdapter(PlatformAdapter):
    platform = "tiktok"

    def __init__(self, transport: PlatformTransport | None = None) -> None:
        self.transport = transport or default_transport()

    def build_auth_url(self, redirect_uri: str, state: str, *, site_code: str) -> str:
        app_key, _secret = app_credentials(self.platform)
        return (
            f"{_AUTH_HOST}/oauth/authorize?app_key={quote(app_key, safe='')}"
            f"&state={quote(state, safe='')}"
            f"&redirect_uri={quote(redirect_uri, safe='')}"
            f"&region={site_code.upper()}"
        )

    async def exchange_token(self, code: str, *, site_code: str, shop_id: str | None = None) -> TokenBundle:
        del shop_id
        app_key, secret = app_credentials(self.platform)
        params = {
            "app_key": app_key,
            "auth_code": code,
            "grant_type": "authorized_code",
            "timestamp": str(int(time.time())),
        }
        params["sign"] = tiktok_sign(app_secret=secret, path=_TOKEN_PATH, params=params)
        _status, body = await self.transport.request(
            "GET",
            f"{_AUTH_HOST}{_TOKEN_PATH}",
            params=params,
            platform=self.platform,
        )
        return _named(token_bundle(_payload(body), platform=self.platform, site_code=site_code), site_code)

    async def refresh_token(self, cred: CredentialView) -> TokenBundle:
        app_key, secret = app_credentials(self.platform)
        params = {
            "app_key": app_key,
            "refresh_token": cred.refresh_token or "",
            "grant_type": "refresh_token",
            "timestamp": str(int(time.time())),
        }
        params["sign"] = tiktok_sign(app_secret=secret, path=_REFRESH_PATH, params=params)
        _status, body = await self.transport.request(
            "GET",
            f"{_AUTH_HOST}{_REFRESH_PATH}",
            params=params,
            platform=self.platform,
        )
        bundle = _named(
            token_bundle(_payload(body), platform=self.platform, site_code=cred.site_code),
            cred.site_code,
        )
        shop_id = str(cred.extra.get("platform_shop_id") or bundle.platform_shop_id)
        return TokenBundle(
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token or cred.refresh_token,
            expires_at=bundle.expires_at,
            refresh_expires_at=bundle.refresh_expires_at,
            platform_shop_id=shop_id,
            shop_name=bundle.shop_name,
            extra=bundle.extra,
        )

    async def fetch_orders(
        self,
        cred: CredentialView,
        *,
        since: datetime,
        until: datetime,
        cursor: str | None = None,
    ) -> PageResult[UnifiedOrder]:
        app_key, secret = app_credentials(self.platform)
        params = {"app_key": app_key, "timestamp": str(int(time.time()))}
        params["sign"] = tiktok_sign(app_secret=secret, path=_ORDER_PATH, params=params)
        body: dict[str, Any] = {"create_time_ge": int(since.timestamp()), "create_time_lt": int(until.timestamp())}
        if cursor:
            body["page_token"] = cursor
        _status, raw = await self.transport.request(
            "POST",
            f"{_API_HOST}{_ORDER_PATH}",
            params=params,
            json_body=body,
            platform=self.platform,
        )
        return _tiktok_page(self, raw, cred)

    async def fetch_order(self, cred: CredentialView, platform_order_id: str) -> UnifiedOrder | None:
        app_key, secret = app_credentials(self.platform)
        params = {"app_key": app_key, "timestamp": str(int(time.time())), "ids": platform_order_id}
        params["sign"] = tiktok_sign(app_secret=secret, path=_ORDER_DETAIL_PATH, params=params)
        _status, raw = await self.transport.request(
            "GET",
            f"{_API_HOST}{_ORDER_DETAIL_PATH}",
            params=params,
            platform=self.platform,
        )
        return matching_order(_tiktok_page(self, raw, cred), platform_order_id)

    async def verify_webhook(self, *, body: bytes, headers: Mapping[str, str], callback_url: str) -> bool:
        del callback_url
        app_key, secret = app_credentials(self.platform)
        expected = tiktok_push_sign(app_secret=secret, app_key=app_key, body=body)
        return signatures_match(expected, header_value(headers, "authorization"))

    def parse_webhook(self, body: bytes) -> WebhookEvent:
        payload = load_object(body, platform=self.platform)
        data = nested_dict(payload, "data")
        shop_id = str(payload.get("shop_id") or "")
        order_id = str(data.get("order_id") or "")
        status = str(data.get("order_status") or "")
        try:
            event_type = int(payload.get("type") or 0)
        except (TypeError, ValueError):
            event_type = 0
        kind = WebhookKind.IGNORED.value
        if event_type == _PUSH_ORDER:
            kind = WebhookKind.ORDER_CREATED.value if status == "UNPAID" else WebhookKind.ORDER_STATUS_CHANGED.value
        return WebhookEvent(
            platform=self.platform,
            event_id=coalesce_id("tiktok", payload.get("tts_notification_id"), shop_id, order_id, status),
            kind=kind,
            platform_shop_id=shop_id,
            platform_order_id=order_id or None,
            platform_status=status or None,
            occurred_at=unix_time(data.get("update_time") or payload.get("timestamp")),
            raw=payload,
        )

    async def publish_product(self, cred: CredentialView, product: UnifiedProduct) -> PublishResult:
        return await publish_listing(
            self.transport,
            cred,
            url=f"{_API_HOST}/product/202309/publish_product",
            product=product,
        )

    async def update_price(self, cred: CredentialView, items: list[PriceUpdate]) -> BatchResult:
        return await update_listing_prices(
            self.transport,
            cred,
            url=f"{_API_HOST}/product/202309/update_price",
            items=items,
        )

    async def fetch_listing(
        self,
        cred: CredentialView,
        *,
        platform_product_id: str,
        platform_sku_id: str,
    ) -> RemoteListing:
        return await fetch_remote_listing(
            self.transport,
            cred,
            url=f"{_API_HOST}/product/202309/listing_snapshot",
            platform_product_id=platform_product_id,
            platform_sku_id=platform_sku_id,
        )

    async def ship_order(self, cred: CredentialView, order_id: str, carrier: str, tracking_no: str) -> None:
        await post_shipment(
            self.transport,
            cred,
            url=f"{_API_HOST}/order/202309/orders/ship_order",
            platform_order_id=order_id,
            carrier=carrier,
            tracking_no=tracking_no,
        )

    async def update_address(self, cred: CredentialView, order_id: str, address: dict[str, str]) -> None:
        await post_order_change(
            self.transport,
            cred,
            url=f"{_API_HOST}/order/202309/orders/update_address",
            platform_order_id=order_id,
            fields={"address": address},
        )

    async def update_note(self, cred: CredentialView, order_id: str, content: str) -> None:
        await post_order_change(
            self.transport,
            cred,
            url=f"{_API_HOST}/order/202309/orders/update_note",
            platform_order_id=order_id,
            fields={"content": content},
        )

    def rate_limit(self) -> RateLimitSpec:
        return quota_for(self.platform)

    def status_mapping(self) -> dict[str, str]:
        return {
            "UNPAID": "PENDING",
            "ON_HOLD": "PENDING",
            "AWAITING_SHIPMENT": "PAID",
            "AWAITING_COLLECTION": "PAID",
            "IN_TRANSIT": "SHIPPED",
            "DELIVERED": "DELIVERED",
            "COMPLETED": "COMPLETED",
            "CANCELLED": "CANCELLED",
        }


def _tiktok_page(adapter: TikTokAdapter, raw: dict[str, Any], cred: CredentialView) -> PageResult[UnifiedOrder]:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    rows = data.get("orders") if isinstance(data, dict) else None
    if isinstance(rows, list):
        items: list[UnifiedOrder] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "")
            items.append(tiktok_order(row, shop_id=cred.shop_id, unified_status=adapter.unified_status(status)))
        token = data.get("next_page_token") if isinstance(data, dict) else None
        return PageResult(items=items, next_cursor=str(token) if token else None)
    payload = _payload(raw)
    status = str(payload.get("status") or "")
    order = tiktok_order(payload, shop_id=cred.shop_id, unified_status=adapter.unified_status(status))
    return PageResult(items=[order], next_cursor=None)


def _payload(body: dict[str, Any]) -> dict[str, Any]:
    data = body.get("data")
    return data if isinstance(data, dict) else body


def _named(bundle: TokenBundle, site_code: str) -> TokenBundle:
    shop_id = bundle.platform_shop_id
    if shop_id.startswith("fixture-"):
        shop_id = f"fixture-tiktok-{site_code.upper()}"
    return TokenBundle(
        access_token=bundle.access_token,
        refresh_token=bundle.refresh_token,
        expires_at=bundle.expires_at,
        refresh_expires_at=bundle.refresh_expires_at,
        platform_shop_id=shop_id,
        shop_name=f"TikTok Shop {site_code.upper()}",
        extra=bundle.extra,
    )


__all__ = ["TikTokAdapter"]
