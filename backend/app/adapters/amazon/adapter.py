"""Amazon SP-API 授权（LWA）。订单报文走同一解析器，fixture 与 live 共用。"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from app.adapters.base import CredentialView, PageResult, PlatformAdapter, RateLimitSpec, TokenBundle, UnifiedOrder
from app.adapters.credentials import app_credentials
from app.adapters.oauth_parse import amazon_order, token_bundle
from app.adapters.quotas import quota_for
from app.adapters.signing import amazon_consent_url
from app.adapters.sites import AMAZON_CONSENT_HOST, AMAZON_REGION
from app.adapters.transport import PlatformTransport, default_transport

_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
_ORDERS_URL = "https://sellingpartnerapi-na.amazon.com/orders/v0/orders"


class AmazonAdapter(PlatformAdapter):
    platform = "amazon"

    def __init__(self, transport: PlatformTransport | None = None) -> None:
        self.transport = transport or default_transport()

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
            url = f"{url}&NextToken={cursor}"
        _status, raw = await self.transport.request("GET", url, platform=self.platform)
        status = str(raw.get("OrderStatus") or "")
        order = amazon_order(raw, shop_id=cred.shop_id, unified_status=self.unified_status(status))
        return PageResult(items=[order], next_cursor=None)

    def rate_limit(self) -> RateLimitSpec:
        return quota_for(self.platform)

    def status_mapping(self) -> dict[str, str]:
        return {
            "Pending": "PENDING_PAYMENT",
            "Unshipped": "TO_SHIP",
            "PartiallyShipped": "SHIPPED",
            "Shipped": "SHIPPED",
            "Canceled": "CANCELLED",
        }


__all__ = ["AmazonAdapter"]
