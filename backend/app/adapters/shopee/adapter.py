"""Shopee Open Platform 授权。签名 base string = partner_id + path + timestamp。"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

from app.adapters.base import CredentialView, PageResult, PlatformAdapter, RateLimitSpec, TokenBundle, UnifiedOrder
from app.adapters.credentials import app_credentials
from app.adapters.oauth_parse import shopee_order, token_bundle
from app.adapters.quotas import quota_for
from app.adapters.signing import shopee_sign
from app.adapters.transport import PlatformTransport, default_transport

_HOST = "https://partner.shopeemobile.com"
_AUTH_PATH = "/api/v2/shop/auth_partner"
_TOKEN_PATH = "/api/v2/auth/token/get"
_REFRESH_PATH = "/api/v2/auth/access_token/get"
_ORDER_PATH = "/api/v2/order/get_order_list"


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

    def rate_limit(self) -> RateLimitSpec:
        return quota_for(self.platform)

    def status_mapping(self) -> dict[str, str]:
        return {
            "UNPAID": "PENDING_PAYMENT",
            "READY_TO_SHIP": "TO_SHIP",
            "PROCESSED": "TO_SHIP",
            "SHIPPED": "SHIPPED",
            "COMPLETED": "DELIVERED",
            "CANCELLED": "CANCELLED",
        }


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
