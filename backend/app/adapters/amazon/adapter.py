"""Amazon SP-API 授权（LWA）。订单报文走同一解析器，fixture 与 live 共用。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

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
from app.adapters.amazon.sns import (
    HttpSnsCertSource,
    SnsCertSource,
    amazon_sns_host_allowed,
    parse_amazon_webhook,
    verify_sns_signature,
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
    UnifiedMessage,
    UnifiedOrder,
    UnifiedProduct,
    WebhookEvent,
)
from app.adapters.catalog import fetch_remote_listing, publish_listing, update_listing_prices, update_remote_inventory
from app.adapters.credentials import app_credentials
from app.adapters.errors import AdapterError, RetryDecision
from app.adapters.messages import parse_amazon_messages
from app.adapters.oauth_parse import amazon_order, token_bundle
from app.adapters.quotas import quota_for
from app.adapters.shipping import post_order_change, post_shipment
from app.adapters.signing import amazon_consent_url
from app.adapters.sites import AMAZON_CONSENT_HOST, AMAZON_REGION
from app.adapters.transport import PlatformTransport, default_transport
from app.adapters.webhook_common import load_object, matching_order

_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
_ORDERS_URL = "https://sellingpartnerapi-na.amazon.com/orders/v0/orders"
_ADS_URL = "https://advertising-api.amazon.com/ads/campaigns"
_MESSAGES_URL = "https://sellingpartnerapi-na.amazon.com/messaging/v1/messages"
_AMAZON_STATUS = {"ENABLED": "ENABLED", "PAUSED": "PAUSED", "ARCHIVED": "ARCHIVED"}
_AMAZON_TYPE = {
    "SPONSORED_PRODUCTS": "SPONSORED_PRODUCT",
    "SPONSORED_BRANDS": "SPONSORED_BRAND",
    "SPONSORED_DISPLAY": "SPONSORED_DISPLAY",
}


class AmazonAdapter(PlatformAdapter):
    platform = "amazon"

    def __init__(self, transport: PlatformTransport | None = None, certs: SnsCertSource | None = None) -> None:
        self.transport = transport or default_transport()
        self.certs = certs or HttpSnsCertSource()

    def build_auth_url(self, redirect_uri: str, state: str, *, site_code: str) -> str:
        application_id, _secret = app_credentials(self.platform)
        region = AMAZON_REGION[site_code.upper()]
        return amazon_consent_url(
            host=AMAZON_CONSENT_HOST[region],
            application_id=application_id,
            redirect_uri=redirect_uri,
            state=state,
        )

    async def exchange_token(self, code: str, *, site_code: str, shop_id: str | None = None) -> TokenBundle:
        del shop_id
        _app_id, secret = app_credentials(self.platform)
        _status, body = await self.transport.request(
            "POST",
            _TOKEN_URL,
            json_body={"grant_type": "authorization_code", "code": code, "client_secret": secret},
            platform=self.platform,
        )
        bundle = token_bundle(body, platform=self.platform, site_code=site_code, shop_name=f"Amazon {site_code}")
        return bundle

    async def refresh_token(self, cred: CredentialView) -> TokenBundle:
        _app_id, secret = app_credentials(self.platform)
        _status, body = await self.transport.request(
            "POST",
            _TOKEN_URL,
            json_body={
                "grant_type": "refresh_token",
                "refresh_token": cred.refresh_token or "",
                "client_secret": secret,
            },
            platform=self.platform,
        )
        bundle = token_bundle(
            body, platform=self.platform, site_code=cred.site_code, shop_name=f"Amazon {cred.site_code}"
        )
        if body.get("shop_id") is None and body.get("seller_id") is None:
            return TokenBundle(
                access_token=bundle.access_token,
                refresh_token=bundle.refresh_token or cred.refresh_token,
                expires_at=bundle.expires_at,
                refresh_expires_at=bundle.refresh_expires_at,
                platform_shop_id=cred.extra.get("platform_shop_id", bundle.platform_shop_id),
                shop_name=bundle.shop_name,
                extra=bundle.extra,
            )
        return bundle

    async def fetch_orders(
        self,
        cred: CredentialView,
        *,
        since: datetime,
        until: datetime,
        cursor: str | None = None,
    ) -> PageResult[UnifiedOrder]:
        query = urlencode({"createdAfter": since.isoformat(), "createdBefore": until.isoformat()})
        url = f"{_ORDERS_URL}?{query}"
        if cursor:
            url = f"{url}&NextToken={quote(cursor, safe='')}"
        _status, raw = await self.transport.request("GET", url, platform=self.platform)
        return _amazon_page(self, raw, cred)

    async def fetch_ads(
        self,
        cred: CredentialView,
        *,
        since: datetime,
        until: datetime,
        cursor: str | None = None,
    ) -> PageResult[UnifiedAdCampaign]:
        del cred
        query = urlencode({"startDate": since.date().isoformat(), "endDate": until.date().isoformat()})
        url = f"{_ADS_URL}?{query}"
        if cursor:
            url = f"{url}&nextToken={quote(cursor, safe='')}"
        _status, raw = await self.transport.request("GET", url, platform=self.platform)
        return _amazon_ads(raw)

    async def fetch_messages(
        self,
        cred: CredentialView,
        *,
        cursor: str | None = None,
    ) -> PageResult[UnifiedMessage]:
        del cred
        url = _MESSAGES_URL
        if cursor:
            url = f"{url}?nextToken={quote(cursor, safe='')}"
        _status, raw = await self.transport.request("GET", url, platform=self.platform)
        return parse_amazon_messages(raw)

    async def fetch_order(self, cred: CredentialView, platform_order_id: str) -> UnifiedOrder | None:
        url = f"{_ORDERS_URL}/{quote(platform_order_id, safe='')}"
        _status, raw = await self.transport.request("GET", url, platform=self.platform)
        return matching_order(_amazon_page(self, raw, cred), platform_order_id)

    async def verify_webhook(self, *, body: bytes, headers: Mapping[str, str], callback_url: str) -> bool:
        del headers, callback_url
        try:
            message = load_object(body, platform=self.platform)
        except AdapterError:
            return False
        cert_url = message.get("SigningCertURL")
        if not isinstance(cert_url, str) or not amazon_sns_host_allowed(cert_url):
            return False
        pem = await self.certs.load(cert_url)
        if not pem:
            return False
        return verify_sns_signature(message, pem)

    def parse_webhook(self, body: bytes) -> WebhookEvent:
        return parse_amazon_webhook(body)

    async def publish_product(self, cred: CredentialView, product: UnifiedProduct) -> PublishResult:
        return await publish_listing(
            self.transport,
            cred,
            url=f"{_ORDERS_URL}/publish_product",
            product=product,
        )

    async def update_price(self, cred: CredentialView, items: list[PriceUpdate]) -> BatchResult:
        return await update_listing_prices(
            self.transport,
            cred,
            url=f"{_ORDERS_URL}/update_price",
            items=items,
        )

    async def update_inventory(self, cred: CredentialView, items: list[InventoryUpdate]) -> BatchResult:
        return await update_remote_inventory(
            self.transport,
            cred,
            url=f"{_ORDERS_URL}/update_inventory",
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
            url=f"{_ORDERS_URL}/listing_snapshot",
            platform_product_id=platform_product_id,
            platform_sku_id=platform_sku_id,
        )

    async def ship_order(self, cred: CredentialView, order_id: str, carrier: str, tracking_no: str) -> None:
        url = f"{_ORDERS_URL}/{quote(order_id, safe='')}/ship_order"
        await post_shipment(
            self.transport,
            cred,
            url=url,
            platform_order_id=order_id,
            carrier=carrier,
            tracking_no=tracking_no,
        )

    async def update_address(self, cred: CredentialView, order_id: str, address: dict[str, str]) -> None:
        await post_order_change(
            self.transport,
            cred,
            url=f"{_ORDERS_URL}/{quote(order_id, safe='')}/update_address",
            platform_order_id=order_id,
            fields={"address": address},
        )

    async def update_note(self, cred: CredentialView, order_id: str, content: str) -> None:
        await post_order_change(
            self.transport,
            cred,
            url=f"{_ORDERS_URL}/{quote(order_id, safe='')}/update_note",
            platform_order_id=order_id,
            fields={"content": content},
        )

    def rate_limit(self) -> RateLimitSpec:
        return quota_for(self.platform)

    def status_mapping(self) -> dict[str, str]:
        return {
            "Pending": "PENDING",
            "PendingAvailability": "PENDING",
            "Unshipped": "PAID",
            "InvoiceUnconfirmed": "PAID",
            "PartiallyShipped": "SHIPPED",
            "Shipped": "SHIPPED",
            "Canceled": "CANCELLED",
            "Unfulfillable": "CANCELLED",
        }


def _amazon_ads(raw: dict[str, Any]) -> PageResult[UnifiedAdCampaign]:
    rows = raw.get("campaigns")
    if not isinstance(rows, list):
        raise AdapterError("Amazon 广告报表缺少 campaigns", platform="amazon", decision=RetryDecision.FAIL_FAST)
    items = [_amazon_campaign(row) for row in rows]
    token = raw.get("nextToken")
    return PageResult(items=items, next_cursor=str(token) if token else None)


def _amazon_campaign(row: object) -> UnifiedAdCampaign:
    if not isinstance(row, dict):
        raise AdapterError("Amazon 广告活动不是对象", platform="amazon", decision=RetryDecision.FAIL_FAST)
    metrics = row.get("metrics")
    if not isinstance(metrics, list):
        raise AdapterError("Amazon 广告日指标缺失", platform="amazon", decision=RetryDecision.FAIL_FAST)
    state = str(row.get("state") or "").upper()
    product = str(row.get("adProduct") or "").upper()
    return build_campaign(
        platform="amazon",
        platform_campaign_id=text_field(row.get("campaignId"), platform="amazon", field="campaignId"),
        name=text_field(row.get("name"), platform="amazon", field="name"),
        campaign_type=_AMAZON_TYPE.get(product, "OTHER"),
        status=_AMAZON_STATUS.get(state, "UNKNOWN"),
        currency=currency_field(row.get("currency"), platform="amazon"),
        platform_sku_id=optional_text(row.get("sku")),
        days=tuple(_amazon_day(item, currency_field(row.get("currency"), platform="amazon")) for item in metrics),
    )


def _amazon_day(row: object, currency: str) -> UnifiedAdDay:
    if not isinstance(row, dict):
        raise AdapterError("Amazon 广告日指标不是对象", platform="amazon", decision=RetryDecision.FAIL_FAST)
    keywords = row.get("keywords", [])
    if not isinstance(keywords, list):
        raise AdapterError("Amazon 关键词报表不是列表", platform="amazon", decision=RetryDecision.FAIL_FAST)
    return build_day(
        platform="amazon",
        stat_date=day_field(row.get("date"), platform="amazon"),
        impressions=count_field(row.get("impressions"), platform="amazon", field="impressions"),
        clicks=count_field(row.get("clicks"), platform="amazon", field="clicks"),
        spend=money_field(row.get("cost"), platform="amazon", field="cost"),
        sales=money_field(row.get("sales"), platform="amazon", field="sales"),
        orders=count_field(row.get("purchases"), platform="amazon", field="purchases"),
        currency=currency,
        keywords=tuple(_amazon_keyword(item) for item in keywords),
    )


def _amazon_keyword(row: object) -> UnifiedAdKeyword:
    if not isinstance(row, dict):
        raise AdapterError("Amazon 关键词不是对象", platform="amazon", decision=RetryDecision.FAIL_FAST)
    return build_keyword(
        platform="amazon",
        keyword=text_field(row.get("keyword"), platform="amazon", field="keyword"),
        impressions=count_field(row.get("impressions"), platform="amazon", field="impressions"),
        clicks=count_field(row.get("clicks"), platform="amazon", field="clicks"),
        spend=money_field(row.get("cost"), platform="amazon", field="cost"),
        sales=money_field(row.get("sales"), platform="amazon", field="sales"),
        orders=count_field(row.get("purchases"), platform="amazon", field="purchases"),
    )


def _amazon_page(adapter: AmazonAdapter, raw: dict[str, Any], cred: CredentialView) -> PageResult[UnifiedOrder]:
    body = raw.get("payload") if isinstance(raw.get("payload"), dict) else raw
    rows = body.get("Orders") if isinstance(body, dict) else None
    if isinstance(rows, list):
        items: list[UnifiedOrder] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = str(row.get("OrderStatus") or "")
            items.append(amazon_order(row, shop_id=cred.shop_id, unified_status=adapter.unified_status(status)))
        token = body.get("NextToken") if isinstance(body, dict) else None
        return PageResult(items=items, next_cursor=str(token) if token else None)
    status = str(raw.get("OrderStatus") or "")
    order = amazon_order(raw, shop_id=cred.shop_id, unified_status=adapter.unified_status(status))
    return PageResult(items=[order], next_cursor=None)


__all__ = ["AmazonAdapter"]
