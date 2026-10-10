"""Lazada Open Platform 授权。支持 SG/MY/TH/ID/VN/PH。"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import quote

from app.adapters.ads_fields import (
    build_campaign,
    build_day,
    build_keyword,
    count_field,
    currency_field,
    day_field,
    money_field,
    optional_text,
    text_field,
)
from app.adapters.base import (
    BatchResult,
    CredentialView,
    InventoryUpdate,
    PageResult,
    PlatformAdapter,
    PriceUpdate,
    PublishResult,
    RateLimitSpec,
    RemoteListing,
    TokenBundle,
    UnifiedAdCampaign,
    UnifiedAdDay,
    UnifiedAdKeyword,
    UnifiedOrder,
    UnifiedProduct,
    WebhookEvent,
    WebhookKind,
)
from app.adapters.catalog import fetch_remote_listing, publish_listing, update_listing_prices, update_remote_inventory
from app.adapters.credentials import app_credentials
from app.adapters.errors import AdapterError, RetryDecision
from app.adapters.oauth_parse import lazada_order, token_bundle
from app.adapters.quotas import quota_for
from app.adapters.shipping import post_order_change, post_shipment
from app.adapters.signing import lazada_push_sign, lazada_sign, signatures_match
from app.adapters.sites import LAZADA_AUTH_HOST
from app.adapters.transport import PlatformTransport, default_transport
from app.adapters.webhook_common import (
    as_int,
    coalesce_id,
    header_value,
    load_object,
    matching_order,
    nested_dict,
    unix_time,
)

_TOKEN_PATH = "/rest/auth/token/create"
_REFRESH_PATH = "/rest/auth/token/refresh"
_ORDERS_PATH = "/orders/get"
_ORDER_DETAIL_PATH = "/order/get"
_ADS_PATH = "/ads/campaign/get"
_LAZADA_STATUS = {1: "ENABLED", 0: "PAUSED", 9: "ARCHIVED"}
_LAZADA_TYPE = {"SPONSORED": "SPONSORED_PRODUCT"}
_PUSH_ORDER = 0


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

    async def fetch_ads(
        self,
        cred: CredentialView,
        *,
        since: datetime,
        until: datetime,
        cursor: str | None = None,
    ) -> PageResult[UnifiedAdCampaign]:
        app_key, secret = app_credentials(self.platform)
        params = {
            "app_key": app_key,
            "sign_method": "sha256",
            "timestamp": str(int(time.time() * 1000)),
            "start_date": since.date().isoformat(),
            "end_date": until.date().isoformat(),
        }
        if cursor:
            params["page_token"] = cursor
        params["sign"] = lazada_sign(app_secret=secret, path=_ADS_PATH, params=params)
        host = LAZADA_AUTH_HOST[cred.site_code.upper()]
        _status, raw = await self.transport.request("GET", f"{host}{_ADS_PATH}", params=params, platform=self.platform)
        return _lazada_ads(raw)

    async def fetch_order(self, cred: CredentialView, platform_order_id: str) -> UnifiedOrder | None:
        app_key, secret = app_credentials(self.platform)
        params = {
            "app_key": app_key,
            "sign_method": "sha256",
            "timestamp": str(int(time.time() * 1000)),
            "order_id": platform_order_id,
        }
        params["sign"] = lazada_sign(app_secret=secret, path=_ORDER_DETAIL_PATH, params=params)
        host = LAZADA_AUTH_HOST[cred.site_code.upper()]
        _status, raw = await self.transport.request(
            "GET",
            f"{host}{_ORDER_DETAIL_PATH}",
            params=params,
            platform=self.platform,
        )
        return matching_order(_lazada_page(self, raw, cred), platform_order_id)

    async def verify_webhook(self, *, body: bytes, headers: Mapping[str, str], callback_url: str) -> bool:
        del callback_url
        _app_key, secret = app_credentials(self.platform)
        expected = lazada_push_sign(app_secret=secret, body=body)
        return signatures_match(expected, header_value(headers, "authorization"))

    def parse_webhook(self, body: bytes) -> WebhookEvent:
        payload = load_object(body, platform=self.platform)
        data = nested_dict(payload, "data")
        seller_id = str(payload.get("seller_id") or "")
        order_id = str(data.get("trade_order_id") or "")
        status = str(data.get("order_status") or "")
        message_type = as_int(payload.get("message_type"), default=-1)
        kind = WebhookKind.IGNORED.value
        if message_type == _PUSH_ORDER:
            kind = WebhookKind.ORDER_CREATED.value if status == "unpaid" else WebhookKind.ORDER_STATUS_CHANGED.value
        return WebhookEvent(
            platform=self.platform,
            event_id=coalesce_id(
                "lazada",
                seller_id,
                order_id,
                status,
                data.get("status_update_time") or payload.get("timestamp"),
            ),
            kind=kind,
            platform_shop_id=seller_id,
            platform_order_id=order_id or None,
            platform_status=status or None,
            occurred_at=unix_time(data.get("status_update_time") or payload.get("timestamp")),
            raw=payload,
        )

    async def publish_product(self, cred: CredentialView, product: UnifiedProduct) -> PublishResult:
        host = _lazada_host(self.platform, cred)
        return await publish_listing(self.transport, cred, url=f"{host}/product/publish_product", product=product)

    async def update_price(self, cred: CredentialView, items: list[PriceUpdate]) -> BatchResult:
        host = _lazada_host(self.platform, cred)
        return await update_listing_prices(self.transport, cred, url=f"{host}/product/update_price", items=items)

    async def update_inventory(self, cred: CredentialView, items: list[InventoryUpdate]) -> BatchResult:
        host = _lazada_host(self.platform, cred)
        return await update_remote_inventory(self.transport, cred, url=f"{host}/product/update_inventory", items=items)

    async def fetch_listing(
        self,
        cred: CredentialView,
        *,
        platform_product_id: str,
        platform_sku_id: str,
    ) -> RemoteListing:
        host = _lazada_host(self.platform, cred)
        return await fetch_remote_listing(
            self.transport,
            cred,
            url=f"{host}/product/listing_snapshot",
            platform_product_id=platform_product_id,
            platform_sku_id=platform_sku_id,
        )

    async def ship_order(self, cred: CredentialView, order_id: str, carrier: str, tracking_no: str) -> None:
        host = LAZADA_AUTH_HOST.get(cred.site_code.upper())
        if host is None:
            raise AdapterError(
                "站点不支持发货回传",
                platform=self.platform,
                decision=RetryDecision.FAIL_FAST,
            )
        await post_shipment(
            self.transport,
            cred,
            url=f"{host}/order/ship_order",
            platform_order_id=order_id,
            carrier=carrier,
            tracking_no=tracking_no,
        )

    async def update_address(self, cred: CredentialView, order_id: str, address: dict[str, str]) -> None:
        host = _lazada_host(self.platform, cred)
        await post_order_change(
            self.transport,
            cred,
            url=f"{host}/order/update_address",
            platform_order_id=order_id,
            fields={"address": address},
        )

    async def update_note(self, cred: CredentialView, order_id: str, content: str) -> None:
        host = _lazada_host(self.platform, cred)
        await post_order_change(
            self.transport,
            cred,
            url=f"{host}/order/update_note",
            platform_order_id=order_id,
            fields={"content": content},
        )

    def rate_limit(self) -> RateLimitSpec:
        return quota_for(self.platform)

    def status_mapping(self) -> dict[str, str]:
        return {
            "unpaid": "PENDING",
            "pending": "PAID",
            "packed": "PAID",
            "repacked": "PAID",
            "ready_to_ship": "PAID",
            "shipped": "SHIPPED",
            "delivered": "DELIVERED",
            "confirmed": "COMPLETED",
            "canceled": "CANCELLED",
            "returned": "RETURNED",
            "failed": "CANCELLED",
            "shipped_back": "RETURNED",
            "shipped_back_success": "RETURNED",
        }


def _lazada_host(platform: str, cred: CredentialView) -> str:
    host = LAZADA_AUTH_HOST.get(cred.site_code.upper())
    if host is None:
        raise AdapterError(
            "站点不支持订单回传",
            platform=platform,
            decision=RetryDecision.FAIL_FAST,
        )
    return host


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


def _lazada_ads(raw: dict[str, Any]) -> PageResult[UnifiedAdCampaign]:
    body = raw.get("data") if isinstance(raw.get("data"), dict) else None
    rows = body.get("campaigns") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not isinstance(body, dict):
        raise AdapterError("Lazada 广告报表缺少 campaigns", platform="lazada", decision=RetryDecision.FAIL_FAST)
    items = [_lazada_campaign(row) for row in rows]
    token = body.get("page_token")
    return PageResult(items=items, next_cursor=str(token) if token else None)


def _lazada_campaign(row: object) -> UnifiedAdCampaign:
    if not isinstance(row, dict):
        raise AdapterError("Lazada 广告活动不是对象", platform="lazada", decision=RetryDecision.FAIL_FAST)
    report = row.get("report")
    if not isinstance(report, list):
        raise AdapterError("Lazada 广告日指标缺失", platform="lazada", decision=RetryDecision.FAIL_FAST)
    online = row.get("onlineStatus")
    kind = str(row.get("campaignType") or "").upper()
    currency = currency_field(row.get("currency"), platform="lazada")
    return build_campaign(
        platform="lazada",
        platform_campaign_id=text_field(row.get("campaignId"), platform="lazada", field="campaignId"),
        name=text_field(row.get("campaignName"), platform="lazada", field="campaignName"),
        campaign_type=_LAZADA_TYPE.get(kind, "OTHER"),
        status=_LAZADA_STATUS.get(online, "UNKNOWN") if isinstance(online, int) else "UNKNOWN",
        currency=currency,
        platform_sku_id=optional_text(row.get("skuId")),
        days=tuple(_lazada_day(item, currency) for item in report),
    )


def _lazada_day(row: object, currency: str) -> UnifiedAdDay:
    if not isinstance(row, dict):
        raise AdapterError("Lazada 广告日指标不是对象", platform="lazada", decision=RetryDecision.FAIL_FAST)
    keywords = row.get("keywords", [])
    if not isinstance(keywords, list):
        raise AdapterError("Lazada 关键词报表不是列表", platform="lazada", decision=RetryDecision.FAIL_FAST)
    return build_day(
        platform="lazada",
        stat_date=day_field(row.get("date"), platform="lazada"),
        impressions=count_field(row.get("impressions"), platform="lazada", field="impressions"),
        clicks=count_field(row.get("clicks"), platform="lazada", field="clicks"),
        spend=money_field(row.get("spend"), platform="lazada", field="spend"),
        sales=money_field(row.get("revenue"), platform="lazada", field="revenue"),
        orders=count_field(row.get("orders"), platform="lazada", field="orders"),
        currency=currency,
        keywords=tuple(_lazada_keyword(item) for item in keywords),
    )


def _lazada_keyword(row: object) -> UnifiedAdKeyword:
    if not isinstance(row, dict):
        raise AdapterError("Lazada 关键词不是对象", platform="lazada", decision=RetryDecision.FAIL_FAST)
    return build_keyword(
        platform="lazada",
        keyword=text_field(row.get("keyword"), platform="lazada", field="keyword"),
        impressions=count_field(row.get("impressions"), platform="lazada", field="impressions"),
        clicks=count_field(row.get("clicks"), platform="lazada", field="clicks"),
        spend=money_field(row.get("spend"), platform="lazada", field="spend"),
        sales=money_field(row.get("revenue"), platform="lazada", field="revenue"),
        orders=count_field(row.get("orders"), platform="lazada", field="orders"),
    )


__all__ = ["LazadaAdapter"]
