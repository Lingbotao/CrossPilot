"""Webhook 异步处理。

订单事件拉平台详情再入库，来源记成 WEBHOOK。
库存和客服推送先收下，对账和拉消息分别留到 M3、M5。
"""

from __future__ import annotations

from typing import Protocol

import httpx

from app.adapters.amazon.sns import amazon_sns_host_allowed
from app.adapters.base import WebhookKind
from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError, RetryDecision
from app.adapters.registry import adapter_registry
from app.adapters.transport import use_fixture_transport
from app.core.context import tenant_context
from app.core.logging import get_logger
from app.db.session import owner_session_scope, session_scope
from app.engines.order_status import StatusChangeSource
from app.repositories.platform import ShopCredentialRepository, ShopRepository
from app.services.credential_service import view_from_row
from app.services.order_status import OrderStatusService
from app.sync_engine.idempotency import WriteAction

log = get_logger(__name__)

ORDER_EVENT_SOURCE = StatusChangeSource.WEBHOOK


class WebhookProcessError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class OrderSink(Protocol):
    async def apply(self, *, platform: str, platform_shop_id: str, platform_order_id: str) -> dict[str, object]: ...


class WebhookDispatcher:
    def __init__(self, orders: OrderSink | None = None) -> None:
        self._orders = orders or LiveOrderSink()
        register_builtin_adapters()

    async def handle(self, *, platform: str, body: bytes) -> dict[str, object]:
        event = adapter_registry.get(platform).parse_webhook(body)
        if event.kind == WebhookKind.IGNORED.value:
            return {"status": "ignored", "kind": event.kind}
        if event.kind == WebhookKind.SUBSCRIPTION_CONFIRM.value:
            return await confirm_subscription(str(event.raw.get("subscribe_url") or ""))
        if event.kind == WebhookKind.INVENTORY_CHANGED.value:
            log.info("webhook_deferred", platform=platform, kind=event.kind, milestone="M3")
            return {"status": "deferred", "kind": event.kind, "milestone": "M3"}
        if event.kind == WebhookKind.MESSAGE_CREATED.value:
            log.info("webhook_deferred", platform=platform, kind=event.kind, milestone="M5")
            return {"status": "deferred", "kind": event.kind, "milestone": "M5"}
        if event.kind not in {WebhookKind.ORDER_CREATED.value, WebhookKind.ORDER_STATUS_CHANGED.value}:
            return {"status": "ignored", "kind": event.kind}
        if not event.platform_shop_id or not event.platform_order_id:
            raise WebhookProcessError("订单事件缺少店铺或订单号", retryable=False)
        result = await self._orders.apply(
            platform=platform,
            platform_shop_id=event.platform_shop_id,
            platform_order_id=event.platform_order_id,
        )
        return {"status": "applied", "kind": event.kind, **result}


class LiveOrderSink:
    async def apply(self, *, platform: str, platform_shop_id: str, platform_order_id: str) -> dict[str, object]:
        located = await _locate_shop(platform, platform_shop_id)
        if located is None:
            log.warning("webhook_shop_unbound", platform=platform, platform_shop_id=platform_shop_id)
            return {"action": "ignored", "reason": "shop_not_found"}
        tenant_id, shop_id = located
        with tenant_context(tenant_id):
            return await _ingest(tenant_id=tenant_id, shop_id=shop_id, platform_order_id=platform_order_id)


async def confirm_subscription(subscribe_url: str) -> dict[str, object]:
    if not amazon_sns_host_allowed(subscribe_url):
        log.warning("webhook_subscribe_url_rejected")
        return {"status": "ignored", "reason": "subscribe_url"}
    if use_fixture_transport():
        log.info("webhook_subscribe_skipped", reason="fixture")
        return {"status": "skipped", "reason": "fixture"}
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            response = await client.get(subscribe_url)
    except httpx.HTTPError as exc:
        raise WebhookProcessError("订阅确认失败", retryable=True) from exc
    if response.status_code >= 400:
        raise WebhookProcessError("订阅确认失败", retryable=True)
    return {"status": "confirmed"}


async def dispatch_webhook_body(
    *, platform: str, body: bytes, dispatcher: WebhookDispatcher | None = None
) -> dict[str, object]:
    active = dispatcher or WebhookDispatcher()
    try:
        return await active.handle(platform=platform, body=body)
    except AdapterError as exc:
        raise WebhookProcessError(str(exc), retryable=exc.decision is RetryDecision.RETRY) from exc


async def _locate_shop(platform: str, platform_shop_id: str) -> tuple[int, int] | None:
    async with owner_session_scope() as session:
        rows = await ShopRepository(session).list_active_identities(
            platform_code=platform,
            platform_shop_id=platform_shop_id,
        )
    if len(rows) != 1:
        if len(rows) > 1:
            log.error("webhook_shop_ambiguous", platform=platform, platform_shop_id=platform_shop_id, matches=len(rows))
        return None
    return rows[0]


async def _ingest(*, tenant_id: int, shop_id: int, platform_order_id: str) -> dict[str, object]:
    async with session_scope(tenant_id) as session:
        shop = await ShopRepository(session).get_or_404(shop_id)
        cred = await ShopCredentialRepository(session).get_by_shop_id(shop.id)
        if cred is None:
            raise WebhookProcessError("店铺凭证不存在", retryable=False)
        try:
            order = await adapter_registry.get(shop.platform_code).fetch_order(
                view_from_row(shop, cred),
                platform_order_id,
            )
        except AdapterError as exc:
            raise WebhookProcessError(str(exc), retryable=exc.decision is not RetryDecision.FAIL_FAST) from exc
        if order is None:
            raise WebhookProcessError("平台没有返回这笔订单", retryable=False)
        action, _decision = await OrderStatusService(session).ingest_order(shop, order, source=ORDER_EVENT_SOURCE)
    return {"action": action.value if isinstance(action, WriteAction) else str(action)}


__all__ = [
    "ORDER_EVENT_SOURCE",
    "LiveOrderSink",
    "OrderSink",
    "WebhookDispatcher",
    "WebhookProcessError",
    "confirm_subscription",
    "dispatch_webhook_body",
]
