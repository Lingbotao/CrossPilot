"""Lazada Open Platform 授权。支持 SG/MY/TH/ID/VN/PH。"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

from app.adapters.base import CredentialView, PageResult, PlatformAdapter, RateLimitSpec, TokenBundle, UnifiedOrder
from app.adapters.credentials import app_credentials
from app.adapters.oauth_parse import lazada_order, token_bundle
from app.adapters.quotas import quota_for
from app.adapters.signing import lazada_sign
from app.adapters.sites import LAZADA_AUTH_HOST
from app.adapters.transport import PlatformTransport, default_transport

_TOKEN_PATH = "/rest/auth/token/create"
_REFRESH_PATH = "/rest/auth/token/refresh"
_ORDERS_PATH = "/orders/get"


class LazadaAdapter(PlatformAdapter):
    platform = "lazada"

    def __init__(self, transport: PlatformTransport | None = None) -> None:
        self.transport = transport or default_transport()

    def build_auth_url(self, redirect_uri: str, state: str, *, site_code: str) -> str:
        app_key, _secret = app_credentials(self.platform)
        host = LAZADA_AUTH_HOST[site_code.upper()]
        return (
            f"{host}/oauth/authorize?response_type=code&force_auth=true"
            f"&redirect_uri={quote(redirect_uri, safe='')}"
            f"&client_id={quote(app_key, safe='')}"
            f"&state={quote(state, safe='')}"
            f"&country={site_code.lower()}"
        )

    async def exchange_token(self, code: str, *, site_code: str, shop_id: str | None = None) -> TokenBundle:
        del shop_id
        app_key, secret = app_credentials(self.platform)
        params = {
            "app_key": app_key,
            "code": code,
            "sign_method": "sha256",
            "timestamp": str(int(time.time() * 1000)),
        }
        params["sign"] = lazada_sign(app_secret=secret, path=_TOKEN_PATH, params=params)
        host = LAZADA_AUTH_HOST[site_code.upper()]
        _status, body = await self.transport.request(
            "POST",
            f"{host}{_TOKEN_PATH}",
            params=params,
            platform=self.platform,
        )
        return _named(token_bundle(body, platform=self.platform, site_code=site_code), site_code)

    async def refresh_token(self, cred: CredentialView) -> TokenBundle:
        app_key, secret = app_credentials(self.platform)
        params = {
            "app_key": app_key,
            "refresh_token": cred.refresh_token or "",
            "sign_method": "sha256",
            "timestamp": str(int(time.time() * 1000)),
            "grant_type": "refresh_token",
        }
        params["sign"] = lazada_sign(app_secret=secret, path=_REFRESH_PATH, params=params)
        host = LAZADA_AUTH_HOST[cred.site_code.upper()]
        _status, body = await self.transport.request(
            "POST",
            f"{host}{_REFRESH_PATH}",
            params=params,
            json_body={"grant_type": "refresh_token"},
            platform=self.platform,
        )
        bundle = _named(token_bundle(body, platform=self.platform, site_code=cred.site_code), cred.site_code)
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
        params = {
            "app_key": app_key,
            "sign_method": "sha256",
            "timestamp": str(int(time.time() * 1000)),
            "created_after": since.isoformat(),
            "update_before": until.isoformat(),
        }
        if cursor:
            params["cursor"] = cursor
        params["sign"] = lazada_sign(app_secret=secret, path=_ORDERS_PATH, params=params)
        host = LAZADA_AUTH_HOST[cred.site_code.upper()]
        _status, raw = await self.transport.request(
            "GET",
            f"{host}{_ORDERS_PATH}",
            params=params,
            platform=self.platform,
        )
        return _lazada_page(self, raw, cred)

    def rate_limit(self) -> RateLimitSpec:
        return quota_for(self.platform)

    def status_mapping(self) -> dict[str, str]:
        return {
            "unpaid": "PENDING_PAYMENT",
            "pending": "TO_SHIP",
            "ready_to_ship": "TO_SHIP",
            "shipped": "SHIPPED",
            "delivered": "DELIVERED",
            "canceled": "CANCELLED",
        }


def _lazada_page(adapter: LazadaAdapter, raw: dict[str, Any], cred: CredentialView) -> PageResult[UnifiedOrder]:
    body = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    rows = body.get("orders") if isinstance(body, dict) else None
    if isinstance(rows, list):
        items: list[UnifiedOrder] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            statuses = row.get("statuses") or []
            status = str(statuses[0] if isinstance(statuses, list) and statuses else "")
            items.append(lazada_order(row, shop_id=cred.shop_id, unified_status=adapter.unified_status(status)))
        token = body.get("next_cursor") if isinstance(body, dict) else None
        return PageResult(items=items, next_cursor=str(token) if token else None)
    statuses = raw.get("statuses") or []
    status = str(statuses[0] if isinstance(statuses, list) and statuses else "")
    order = lazada_order(raw, shop_id=cred.shop_id, unified_status=adapter.unified_status(status))
    return PageResult(items=[order], next_cursor=None)


def _named(bundle: TokenBundle, site_code: str) -> TokenBundle:
    shop_id = bundle.platform_shop_id
    if shop_id.startswith("fixture-"):
        shop_id = f"fixture-lazada-{site_code.upper()}"
    return TokenBundle(
        access_token=bundle.access_token,
        refresh_token=bundle.refresh_token,
        expires_at=bundle.expires_at,
        refresh_expires_at=bundle.refresh_expires_at,
        platform_shop_id=shop_id,
        shop_name=f"Lazada {site_code.upper()}",
        extra=bundle.extra,
    )


__all__ = ["LazadaAdapter"]
