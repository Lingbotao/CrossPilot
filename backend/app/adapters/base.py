"""平台适配器统一契约与领域模型（PRD 8.4）。

业务层只依赖本文件里的模型和 ``PlatformAdapter``。平台差异留在各平台实现里。
``reply_message`` 不在 V1 契约中（客服站内回复是 V1.5）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from app.adapters.errors import AdapterError, RetryDecision, classify_platform_error

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class UnifiedOrderItem:
    platform_sku_id: str
    platform_product_id: str
    quantity: int
    unit_price: Decimal
    item_name: str


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
    """限流规格。数值来自 ``quotas`` 配置，不在业务代码里写死。"""

    qps: int
    burst: int
    dimension: str
    batch_limit: int


@dataclass(frozen=True, slots=True)
class PublishResult:
    platform_product_id: str
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
class InventoryUpdate:
    platform_sku_id: str
    available: int


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

    async def update_inventory(self, cred: CredentialView, items: list[InventoryUpdate]) -> BatchResult:
        raise self._later("M3", cred)

    async def ship_order(self, cred: CredentialView, order_id: str, carrier: str, tracking_no: str) -> None:
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
        return self.status_mapping().get(platform_status, "PAID")

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
    "TokenBundle",
    "UnifiedInventory",
    "UnifiedOrder",
    "UnifiedOrderItem",
    "UnifiedProduct",
]
