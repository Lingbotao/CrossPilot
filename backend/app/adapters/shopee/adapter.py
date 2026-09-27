"""Shopee Open Platform 授权。签名 base string = partner_id + path + timestamp。"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import quote

from app.adapters.base import (
    CredentialView,
    PageResult,
    PlatformAdapter,
    RateLimitSpec,
    TokenBundle,
    UnifiedOrder,
    WebhookEvent,
    WebhookKind,
)
from app.adapters.credentials import app_credentials
from app.adapters.oauth_parse import shopee_order, token_bundle
from app.adapters.quotas import quota_for
from app.adapters.shipping import post_order_change, post_shipment
from app.adapters.signing import shopee_push_sign, shopee_sign, signatures_match
from app.adapters.transport import PlatformTransport, default_transport
from app.adapters.webhook_common import coalesce_id, header_value, load_object, matching_order, nested_dict, unix_time

_HOST = "https://partner.shopeemobile.com"
_AUTH_PATH = "/api/v2/shop/auth_partner"
_TOKEN_PATH = "/api/v2/auth/token/get"
_REFRESH_PATH = "/api/v2/auth/access_token/get"
_ORDER_PATH = "/api/v2/order/get_order_list"
_ORDER_DETAIL_PATH = "/api/v2/order/get_order_detail"
_PUSH_ORDER = 3
_PUSH_STOCK = 8
_PUSH_CHAT = 10


class ShopeeAdapter(PlatformAdapter):
    platform = "shopee"

    def __init__(self, transport: PlatformTransport | None = None) -> None:
        self.transport = transport or default_transport()

    def build_auth_url(self, redirect_uri: str, state: str, *, site_code: str) -> str:
        partner_id, partner_key = app_credentials(self.platform)
        timestamp = int(time.time())
        sign = shopee_sign(partner_id=partner_id, partner_key=partner_key, path=_AUTH_PATH, timestamp=timestamp)
        return (
            f"{_HOST}{_AUTH_PATH}?partner_id={partner_id}&timestamp={timestamp}"
            f"&sign={sign}&redirect={quote(redirect_uri, safe='')}"
            f"&state={quote(state, safe='')}&region={site_code.upper()}"
        )

    async def exchange_token(self, code: str, *, site_code: str, shop_id: str | None = None) -> TokenBundle:
        partner_id, partner_key = app_credentials(self.platform)
        timestamp = int(time.time())
        sign = shopee_sign(partner_id=partner_id, partner_key=partner_key, path=_TOKEN_PATH, timestamp=timestamp)
        url = f"{_HOST}{_TOKEN_PATH}?partner_id={partner_id}&timestamp={timestamp}&sign={sign}"
        _status, body = await self.transport.request(
            "POST",
            url,
            json_body={
                "code": code,
                "shop_id": shop_id,
                "partner_id": int(partner_id) if partner_id.isdigit() else partner_id,
            },
            platform=self.platform,
        )
        bundle = token_bundle(body, platform=self.platform, site_code=site_code, shop_name=f"Shopee {site_code}")
        if not body.get("shop_id"):
            return _with_shop_id(bundle, f"fixture-shopee-{site_code.upper()}")
        return bundle

    async def refresh_token(self, cred: CredentialView) -> TokenBundle:
        partner_id, partner_key = app_credentials(self.platform)
        timestamp = int(time.time())
        sign = shopee_sign(partner_id=partner_id, partner_key=partner_key, path=_REFRESH_PATH, timestamp=timestamp)
        url = f"{_HOST}{_REFRESH_PATH}?partner_id={partner_id}&timestamp={timestamp}&sign={sign}"
        _status, body = await self.transport.request(
            "POST",
            url,
            json_body={
                "refresh_token": cred.refresh_token,
                "shop_id": cred.extra.get("platform_shop_id"),
                "grant_type": "refresh_token",
            },
            platform=self.platform,
        )
        bundle = token_bundle(
            body, platform=self.platform, site_code=cred.site_code, shop_name=f"Shopee {cred.site_code}"
        )
        platform_shop_id = str(body.get("shop_id") or cred.extra.get("platform_shop_id") or bundle.platform_shop_id)
        return _with_shop_id(bundle, platform_shop_id)

    async def fetch_orders(
        self,
        cred: CredentialView,
        *,
        since: datetime,
        until: datetime,
        cursor: str | None = None,
    ) -> PageResult[UnifiedOrder]:
        partner_id, partner_key = app_credentials(self.platform)
        timestamp = int(time.time())
        sign = shopee_sign(partner_id=partner_id, partner_key=partner_key, path=_ORDER_PATH, timestamp=timestamp)
        url = (
            f"{_HOST}{_ORDER_PATH}?partner_id={partner_id}&timestamp={timestamp}&sign={sign}"
            f"&time_from={int(since.timestamp())}&time_to={int(until.timestamp())}"
        )
        if cursor:
            url = f"{url}&cursor={quote(cursor, safe='')}"
        _status, raw = await self.transport.request("GET", url, platform=self.platform)
        return _shopee_page(self, raw, cred)

    async def fetch_order(self, cred: CredentialView, platform_order_id: str) -> UnifiedOrder | None:
        partner_id, partner_key = app_credentials(self.platform)
        timestamp = int(time.time())
        sign = shopee_sign(partner_id=partner_id, partner_key=partner_key, path=_ORDER_DETAIL_PATH, timestamp=timestamp)
        url = f"{_HOST}{_ORDER_DETAIL_PATH}?partner_id={partner_id}&timestamp={timestamp}&sign={sign}"
        _status, raw = await self.transport.request(
            "POST",
            url,
            json_body={"order_sn_list": platform_order_id},
            platform=self.platform,
        )
        return matching_order(_shopee_page(self, raw, cred), platform_order_id)

    async def verify_webhook(self, *, body: bytes, headers: Mapping[str, str], callback_url: str) -> bool:
        _partner_id, partner_key = app_credentials(self.platform)
        expected = shopee_push_sign(partner_key=partner_key, url=callback_url, body=body)
        return signatures_match(expected, header_value(headers, "authorization"))

    def parse_webhook(self, body: bytes) -> WebhookEvent:
        payload = load_object(body, platform=self.platform)
        data = nested_dict(payload, "data")
        shop_id = str(payload.get("shop_id") or "")
        order_sn = str(data.get("ordersn") or data.get("order_sn") or "")
        status = str(data.get("status") or data.get("order_status") or "")
        updated = unix_time(data.get("update_time") or payload.get("timestamp"))
        try:
            code = int(payload.get("code") or 0)
        except (TypeError, ValueError):
            code = 0
        return WebhookEvent(
            platform=self.platform,
            event_id=coalesce_id(
                "shopee",
                payload.get("msg_id"),
                shop_id,
                order_sn,
                status,
                data.get("update_time"),
            ),
            kind=_shopee_kind(code, status),
            platform_shop_id=shop_id,
            platform_order_id=order_sn or None,
            platform_status=status or None,
            occurred_at=updated,
            raw=payload,
        )

    async def ship_order(self, cred: CredentialView, order_id: str, carrier: str, tracking_no: str) -> None:
        await post_shipment(
            self.transport,
            cred,
            url=f"{_HOST}/api/v2/logistics/ship_order",
            platform_order_id=order_id,
            carrier=carrier,
            tracking_no=tracking_no,
        )

    async def update_address(self, cred: CredentialView, order_id: str, address: dict[str, str]) -> None:
        await post_order_change(
            self.transport,
            cred,
            url=f"{_HOST}/api/v2/order/update_address",
            platform_order_id=order_id,
            fields={"address": address},
        )

    async def update_note(self, cred: CredentialView, order_id: str, content: str) -> None:
        await post_order_change(
            self.transport,
            cred,
            url=f"{_HOST}/api/v2/order/update_note",
            platform_order_id=order_id,
            fields={"content": content},
        )

    def rate_limit(self) -> RateLimitSpec:
        return quota_for(self.platform)

    def status_mapping(self) -> dict[str, str]:
        return {
            "UNPAID": "PENDING",
            "READY_TO_SHIP": "PAID",
            "PROCESSED": "PAID",
            "RETRY_SHIP": "PAID",
            "SHIPPED": "SHIPPED",
            "TO_CONFIRM_RECEIVE": "SHIPPED",
            "IN_CANCEL": "REFUNDING",
            "CANCELLED": "CANCELLED",
            "TO_RETURN": "RETURNED",
            "COMPLETED": "COMPLETED",
        }


def _shopee_kind(code: int, status: str) -> str:
    if code == _PUSH_STOCK:
        return WebhookKind.INVENTORY_CHANGED.value
    if code == _PUSH_CHAT:
        return WebhookKind.MESSAGE_CREATED.value
    if code != _PUSH_ORDER:
        return WebhookKind.IGNORED.value
    if status == "UNPAID":
        return WebhookKind.ORDER_CREATED.value
    return WebhookKind.ORDER_STATUS_CHANGED.value


def _with_shop_id(bundle: TokenBundle, platform_shop_id: str) -> TokenBundle:
    return TokenBundle(
        access_token=bundle.access_token,
        refresh_token=bundle.refresh_token,
        expires_at=bundle.expires_at,
        refresh_expires_at=bundle.refresh_expires_at,
        platform_shop_id=platform_shop_id,
        shop_name=bundle.shop_name,
        extra=bundle.extra,
    )


def _shopee_page(adapter: ShopeeAdapter, raw: dict[str, Any], cred: CredentialView) -> PageResult[UnifiedOrder]:
    body = raw.get("response") if isinstance(raw.get("response"), dict) else raw
    rows = body.get("order_list") if isinstance(body, dict) else None
    if isinstance(rows, list):
        items: list[UnifiedOrder] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = str(row.get("order_status") or "")
            items.append(shopee_order(row, shop_id=cred.shop_id, unified_status=adapter.unified_status(status)))
        token = body.get("next_cursor") if isinstance(body, dict) else None
        return PageResult(items=items, next_cursor=str(token) if token else None)
    status = str(raw.get("order_status") or "")
    order = shopee_order(raw, shop_id=cred.shop_id, unified_status=adapter.unified_status(status))
    return PageResult(items=[order], next_cursor=None)


__all__ = ["ShopeeAdapter"]
