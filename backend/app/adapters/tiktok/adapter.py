"""TikTok Shop Partner 授权。"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

from app.adapters.base import CredentialView, PageResult, PlatformAdapter, RateLimitSpec, TokenBundle, UnifiedOrder
from app.adapters.credentials import app_credentials
from app.adapters.oauth_parse import tiktok_order, token_bundle
from app.adapters.quotas import quota_for
from app.adapters.signing import tiktok_sign
from app.adapters.transport import PlatformTransport, default_transport

_AUTH_HOST = "https://auth.tiktok-shops.com"
_API_HOST = "https://open-api.tiktokglobalshop.com"
_TOKEN_PATH = "/api/v2/token/get"
_REFRESH_PATH = "/api/v2/token/refresh"
_ORDER_PATH = "/order/202309/orders/search"


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
        del cursor
        app_key, secret = app_credentials(self.platform)
        params = {"app_key": app_key, "timestamp": str(int(time.time()))}
        params["sign"] = tiktok_sign(app_secret=secret, path=_ORDER_PATH, params=params)
        _status, raw = await self.transport.request(
            "POST",
            f"{_API_HOST}{_ORDER_PATH}",
            params=params,
            json_body={"create_time_ge": int(since.timestamp()), "create_time_lt": int(until.timestamp())},
            platform=self.platform,
        )
        payload = _payload(raw)
        status = str(payload.get("status") or "")
        order = tiktok_order(payload, shop_id=cred.shop_id, unified_status=self.unified_status(status))
        return PageResult(items=[order], next_cursor=None)

    def rate_limit(self) -> RateLimitSpec:
        return quota_for(self.platform)

    def status_mapping(self) -> dict[str, str]:
        return {
            "UNPAID": "PENDING_PAYMENT",
            "AWAITING_SHIPMENT": "TO_SHIP",
            "AWAITING_COLLECTION": "TO_SHIP",
            "IN_TRANSIT": "IN_TRANSIT",
            "DELIVERED": "DELIVERED",
            "CANCELLED": "CANCELLED",
        }


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
