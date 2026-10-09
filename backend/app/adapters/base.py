"""平台适配器统一契约与领域模型（PRD 8.4）。

业务层只依赖本文件里的模型和 ``PlatformAdapter``。平台差异留在各平台实现里。
``reply_message`` 不在 V1 契约中（客服站内回复是 V1.5）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Generic, TypeVar

from app.adapters.errors import AdapterError, RetryDecision, classify_platform_error
from app.engines.order_status import UNIFIED_STATUS_CODES

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class UnifiedOrderItem:
    platform_sku_id: str
    platform_product_id: str
    quantity: int
    unit_price: Decimal
    item_name: str


@dataclass(frozen=True, slots=True)
class UnifiedFee:
    """平台给出的费用行。没有报文就不编造。"""

    fee_type: str
    amount: Decimal
    currency: str
    source: str = "platform"


@dataclass(frozen=True, slots=True)
class UnifiedOrder:
    platform: str
    shop_id: str
    platform_order_id: str
    platform_status: str
    unified_status: str
    buyer_name: str | None
    buyer_country: str | None
    currency: str
    total_amount: Decimal
    items: list[UnifiedOrderItem]
    paid_at: datetime | None
    updated_at: datetime
    raw: dict[str, Any]
    buyer_phone: str | None = None
    ship_state: str | None = None
    ship_city: str | None = None
    ship_line1: str | None = None
    ship_postal: str | None = None
    fees: tuple[UnifiedFee, ...] = ()


@dataclass(frozen=True, slots=True)
class UnifiedProduct:
    platform: str
    platform_product_id: str
    title: str
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UnifiedInventory:
    platform_sku_id: str
    available: int
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TokenBundle:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    refresh_expires_at: datetime | None
    platform_shop_id: str
    shop_name: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CredentialView:
    """解密后的凭证视图。适配器看不到密文，也看不到别的租户。"""

    shop_id: str
    platform: str
    site_code: str
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PageResult(Generic[T]):
    items: list[T]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class RateLimitSpec:
    """限流规格。数值来自 ``quotas`` 或 ``platform_rate_limit``，不在业务代码里写死。

    ``daily_quota`` 为空表示不设 UTC 日上限。``concurrency`` 只随配额保存，令牌桶不读取它。
    """

    qps: int
    burst: int
    dimension: str
    batch_limit: int
    daily_quota: int | None = None
    concurrency: int | None = None
    # 与 quotas.DEFAULT_STOCK_PUSH_LAG_SECONDS 一致。业务推送读取配置行，不读这个默认值。
    stock_push_lag_seconds: int = 600


@dataclass(frozen=True, slots=True)
class PublishResult:
    platform_product_id: str
    platform_sku_id: str
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BatchResult:
    succeeded: int
    failed: int
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    platform_sku_id: str
    price: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class RemoteListing:
    """平台上当前的售价。只比较本地已经保存的字段。"""

    price: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class InventoryUpdate:
    platform_sku_id: str
    available: int


class WebhookKind(StrEnum):
    """平台推送收成的事件。取值与 PRD 10.5 的路由名一致。"""

    ORDER_STATUS_CHANGED = "order.status_changed"
    ORDER_CREATED = "order.created"
    INVENTORY_CHANGED = "inventory.changed"
    MESSAGE_CREATED = "message.created"
    SUBSCRIPTION_CONFIRM = "subscription.confirm"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    """验签通过之后的统一推送。原始报文留在 ``raw``，业务层不猜平台字段。"""

    platform: str
    event_id: str
    kind: str
    platform_shop_id: str
    platform_order_id: str | None = None
    platform_status: str | None = None
    occurred_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PlatformAdapter(ABC):
    """四平台共用的认证与拉取契约。"""

    platform: str

    @abstractmethod
    def build_auth_url(self, redirect_uri: str, state: str, *, site_code: str) -> str: ...

    @abstractmethod
    async def exchange_token(self, code: str, *, site_code: str, shop_id: str | None = None) -> TokenBundle: ...

    @abstractmethod
    async def refresh_token(self, cred: CredentialView) -> TokenBundle: ...

    @abstractmethod
    async def fetch_orders(
        self,
        cred: CredentialView,
        *,
        since: datetime,
        until: datetime,
        cursor: str | None = None,
    ) -> PageResult[UnifiedOrder]: ...

    @abstractmethod
    def rate_limit(self) -> RateLimitSpec: ...

    @abstractmethod
    def status_mapping(self) -> dict[str, str]: ...

    @abstractmethod
    async def verify_webhook(
        self,
        *,
        body: bytes,
        headers: Mapping[str, str],
        callback_url: str,
    ) -> bool:
        """先验签。失败时调用方丢弃报文，不解析、不入库。"""

    @abstractmethod
    def parse_webhook(self, body: bytes) -> WebhookEvent:
        """只在验签通过后调用。"""

    async def fetch_order(self, cred: CredentialView, platform_order_id: str) -> UnifiedOrder | None:
        """按平台订单号拉详情。推送里通常没有金额，不能凭空补。"""
        raise self._later("M2", cred)

    async def fetch_products(self, cred: CredentialView, *, cursor: str | None = None) -> PageResult[UnifiedProduct]:
        raise self._later("M3", cred)

    async def fetch_inventory(self, cred: CredentialView, *, sku_ids: list[str]) -> list[UnifiedInventory]:
        raise self._later("M3", cred)

    async def fetch_messages(self, cred: CredentialView, *, cursor: str | None = None) -> PageResult[dict[str, Any]]:
        """V1 只读客服提醒。站内回复不在本契约里。"""
        raise self._later("M5", cred)

    async def publish_product(self, cred: CredentialView, product: UnifiedProduct) -> PublishResult:
        raise self._later("M3", cred)

    async def update_price(self, cred: CredentialView, items: list[PriceUpdate]) -> BatchResult:
        raise self._later("M3", cred)

    async def fetch_listing(
        self,
        cred: CredentialView,
        *,
        platform_product_id: str,
        platform_sku_id: str,
    ) -> RemoteListing:
        raise self._later("M3", cred)

    async def update_inventory(self, cred: CredentialView, items: list[InventoryUpdate]) -> BatchResult:
        raise self._later("M3", cred)

    async def ship_order(self, cred: CredentialView, order_id: str, carrier: str, tracking_no: str) -> None:
        raise self._later("M2", cred)

    async def update_address(self, cred: CredentialView, order_id: str, address: dict[str, str]) -> None:
        raise self._later("M2", cred)

    async def update_note(self, cred: CredentialView, order_id: str, content: str) -> None:
        raise self._later("M2", cred)

    def normalize_error(self, exc: Exception) -> AdapterError:
        if isinstance(exc, AdapterError):
            return exc
        return AdapterError(
            str(exc) or exc.__class__.__name__,
            platform=self.platform,
            decision=classify_platform_error(platform=self.platform, http_status=None),
        )

    def unified_status(self, platform_status: str) -> str:
        mapped = self.status_mapping().get(platform_status)
        if mapped is None or mapped not in UNIFIED_STATUS_CODES:
            return ""
        return mapped

    def _later(self, milestone: str, _cred: CredentialView) -> AdapterError:
        return AdapterError(
            f"{self.platform} 的该能力在 {milestone} 交付",
            platform=self.platform,
            decision=RetryDecision.FAIL_FAST,
        )


__all__ = [
    "BatchResult",
    "CredentialView",
    "InventoryUpdate",
    "PageResult",
    "PlatformAdapter",
    "PriceUpdate",
    "PublishResult",
    "RateLimitSpec",
    "RemoteListing",
    "TokenBundle",
    "UnifiedFee",
    "UnifiedInventory",
    "UnifiedOrder",
    "UnifiedOrderItem",
    "UnifiedProduct",
    "WebhookEvent",
    "WebhookKind",
]
