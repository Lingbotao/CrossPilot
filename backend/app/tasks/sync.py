"""同步任务。

限流、幂等、重试和熔断在 ``app.sync_engine``。订单拉取、重叠窗口和入库在 ``OrderSyncService``。
Beat 扫描用表 owner 找出到点的店铺，真正拉单时再进入该店铺的租户上下文。
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from app.adapters.errors import RetryDecision
from app.core.context import tenant_context
from app.core.logging import configure_logging, get_logger
from app.services.order_sync import OrderSyncService, SyncRun
from app.sync_engine.errors import StoreUnavailable
from app.sync_engine.locks import (
    BEAT_REFRESH_CREDENTIALS,
    BEAT_SCAN_ADS,
    BEAT_SCAN_DEAD_LETTER,
    BEAT_SCAN_DUE_SHOPS,
    BEAT_SCAN_LISTING_DIFFS,
)
from app.sync_engine.runtime import get_dead_letter_queue, run_beat_singleton
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# 范式 A：租户内任务 —— 必须显式传 tenant_id 并用 tenant_context 包裹
# ---------------------------------------------------------------------------
@celery_app.task(name="sync.shop_orders", bind=True, max_retries=3)
def sync_shop_orders(
    self: Any,
    shop_id: int,
    tenant_id: int,
    trigger: str = "beat",
    platform: str | None = None,
) -> dict[str, Any]:
    """同步单个店铺的订单。

    ``tenant_id`` 是必填参数。入队时带上 ``platform``，任务才会进 ``sync.{platform}``，
    某个平台的故障不会占满其他平台的队列。
    """
    configure_logging()
    with tenant_context(tenant_id):
        log.info("sync_shop_orders_started", shop_id=shop_id, trigger=trigger, platform=platform)
        outcome = asyncio.run(_sync_shop_orders_impl(shop_id, tenant_id, trigger))
    if outcome.decision is RetryDecision.RETRY:
        _retry_or_dead_letter(self, outcome, tenant_id=tenant_id, shop_id=shop_id, trigger=trigger, platform=platform)
    return cast(dict[str, Any], outcome.payload)


# ---------------------------------------------------------------------------
# 范式 B：平台级任务 —— 需要跨租户扫描，必须显式声明跳过租户过滤
# ---------------------------------------------------------------------------
async def _sync_shop_orders_impl(shop_id: int, tenant_id: int, trigger: str) -> SyncRun:
    from app.db.session import session_scope
    from app.models.enums import SyncTrigger
    from app.repositories.platform import ShopRepository

    kind = SyncTrigger.MANUAL if trigger == "manual" else SyncTrigger.SCHEDULED
    async with session_scope(tenant_id) as session:
        shop = await ShopRepository(session).get_or_404(shop_id)
        return await OrderSyncService(session).run(shop, trigger=kind, since=None, until=None)


def _retry_or_dead_letter(
    task: Any,
    outcome: Any,
    *,
    tenant_id: int,
    shop_id: int,
    trigger: str,
    platform: str | None,
) -> None:
    from celery.exceptions import MaxRetriesExceededError

    from app.sync_engine.resilience import RetryAction, make_dead_letter, plan_retry
    from app.sync_engine.runtime import get_dead_letter_queue, get_policy

    plan = plan_retry(
        outcome.decision,
        retry_attempt=int(getattr(task.request, "retries", 0) or 0),
        refresh_attempted=False,
        policy=get_policy(),
        retry_after_seconds=outcome.retry_after_seconds,
    )
    letter = make_dead_letter(
        task_name="sync.shop_orders",
        tenant_id=tenant_id,
        kwargs={"shop_id": shop_id, "tenant_id": tenant_id, "trigger": trigger, "platform": platform},
        error=str(outcome.payload.get("error") or outcome.decision),
    )

    async def _push() -> None:
        await get_dead_letter_queue().push(letter)

    if plan.action is RetryAction.DEAD_LETTER:
        asyncio.run(_push())
        return
    if plan.action is not RetryAction.RETRY:
        return
    try:
        raise task.retry(countdown=plan.delay_seconds)
    except MaxRetriesExceededError:
        asyncio.run(_push())


async def _scan_due_shops_impl() -> dict[str, Any]:
    from app.services.order_sync import enqueue_due_order_syncs

    return await enqueue_due_order_syncs()


@celery_app.task(name="sync.scan_due_shops")
def scan_due_shops() -> dict[str, Any]:
    """扫描到点该同步的店铺并入队。间隔由 ``SYNC_ORDER_INTERVAL_SECONDS`` 决定。"""
    configure_logging()
    return asyncio.run(run_beat_singleton(BEAT_SCAN_DUE_SHOPS, _scan_due_shops_impl))


@celery_app.task(name="sync.refresh_expiring_credentials")
def refresh_expiring_credentials() -> dict[str, Any]:
    """令牌续期巡检：到期前刷新，连续失败达到阈值则告警并标记重新授权。"""
    configure_logging()
    return asyncio.run(run_beat_singleton(BEAT_REFRESH_CREDENTIALS, _refresh_expiring_credentials_impl))


async def _refresh_expiring_credentials_impl() -> dict[str, Any]:
    from app.services.credential_service import refresh_expiring_credentials as _refresh

    result = await _refresh()
    log.info("refresh_expiring_credentials_done", **result)
    return result


async def _scan_dead_letter_impl() -> dict[str, Any]:
    try:
        count = await get_dead_letter_queue().size()
    except StoreUnavailable:
        log.warning("dead_letter_scan_unavailable")
        return {"dead_letters": 0, "status": "unavailable"}
    if count:
        log.warning("dead_letter_pending", dead_letters=count)
    return {"dead_letters": count, "status": "ok"}


@celery_app.task(name="sync.scan_dead_letter")
def scan_dead_letter() -> dict[str, Any]:
    """死信队列巡检：有任务待重放就告警。dlq 本身不自动消费。"""
    configure_logging()
    return asyncio.run(run_beat_singleton(BEAT_SCAN_DEAD_LETTER, _scan_dead_letter_impl))


@celery_app.task(name="sync.webhook_event", bind=True, max_retries=3)
def webhook_event(self: Any, platform: str, body: str, event_id: str, kind: str = "") -> dict[str, Any]:
    """处理一条已验签的平台推送。``platform`` 决定进哪个平台队列。"""
    configure_logging()
    from app.services.webhook_dispatch import WebhookProcessError, dispatch_webhook_body

    try:
        return asyncio.run(dispatch_webhook_body(platform=platform, body=body.encode()))
    except WebhookProcessError as exc:
        if exc.retryable:
            _retry_webhook(self, platform=platform, body=body, event_id=event_id, kind=kind, error=str(exc))
        else:
            asyncio.run(_park_webhook(platform=platform, body=body, event_id=event_id, kind=kind, error=str(exc)))
        return {"status": "failed", "error": str(exc)}


def _retry_webhook(task: Any, *, platform: str, body: str, event_id: str, kind: str, error: str) -> None:
    from celery.exceptions import MaxRetriesExceededError

    from app.adapters.errors import RetryDecision
    from app.sync_engine.resilience import RetryAction, plan_retry
    from app.sync_engine.runtime import get_policy

    plan = plan_retry(
        RetryDecision.RETRY,
        retry_attempt=int(getattr(task.request, "retries", 0) or 0),
        refresh_attempted=False,
        policy=get_policy(),
    )
    if plan.action is not RetryAction.RETRY:
        asyncio.run(_park_webhook(platform=platform, body=body, event_id=event_id, kind=kind, error=error))
        return
    try:
        raise task.retry(countdown=plan.delay_seconds)
    except MaxRetriesExceededError:
        asyncio.run(_park_webhook(platform=platform, body=body, event_id=event_id, kind=kind, error=error))


async def _patrol_listing_diffs(tenant_id: int) -> dict[str, Any]:
    from app.db.session import session_scope
    from app.services.listing_diff import ListingDiffService

    async with session_scope(tenant_id) as session:
        result = await ListingDiffService(session).patrol(actor_id=None)
    return {"created": result.created, "scanned": result.scanned}


@celery_app.task(name="sync.patrol_listing_diffs")
def patrol_listing_diffs(tenant_id: int) -> dict[str, Any]:
    """巡检一个租户的已关联 Listing。必须显式传入 tenant_id。"""

    configure_logging()
    with tenant_context(tenant_id):
        return asyncio.run(_patrol_listing_diffs(tenant_id))


async def _scan_listing_diffs() -> dict[str, Any]:
    from app.services.listing_diff import list_linked_tenants

    tenant_ids = await list_linked_tenants()
    for tenant_id in tenant_ids:
        patrol_listing_diffs.delay(tenant_id)
    return {"tenants": len(tenant_ids)}


async def _sync_shop_ads(shop_id: int, tenant_id: int, trigger: str) -> dict[str, Any]:
    from app.db.session import session_scope
    from app.models.enums import SyncTrigger
    from app.schemas.ads import AdsSyncRequest
    from app.services.ads import AdsService

    kind = SyncTrigger.MANUAL if trigger == "manual" else SyncTrigger.SCHEDULED
    async with session_scope(tenant_id) as session:
        view = await AdsService(session).sync(
            AdsSyncRequest(shop_id=shop_id),
            tenant_id=tenant_id,
            actor_id=None,
            idempotency_key=None,
            trigger=kind,
        )
    return view.model_dump()


@celery_app.task(name="sync.shop_ads")
def sync_shop_ads(
    shop_id: int,
    tenant_id: int,
    trigger: str = "beat",
    platform: str | None = None,
) -> dict[str, Any]:
    """同步一个店铺的广告。必须显式传入 tenant_id。只读，不改投放。"""
    del platform
    configure_logging()
    with tenant_context(tenant_id):
        return asyncio.run(_sync_shop_ads(shop_id, tenant_id, trigger))


async def _scan_ads() -> dict[str, Any]:
    from app.db.session import owner_session_scope
    from app.repositories.platform import ShopRepository

    async with owner_session_scope() as session:
        shops = await ShopRepository(session).list_active_across_tenants()
    for tenant_id, shop_id, platform in shops:
        sync_shop_ads.delay(shop_id, tenant_id, trigger="beat", platform=platform)
    log.info("ads_scan_enqueued", shops=len(shops))
    return {"shops": len(shops)}


@celery_app.task(name="sync.scan_ads")
def scan_ads() -> dict[str, Any]:
    """按配置间隔找出仍在授权中的店铺，再按租户入队广告拉取。"""
    configure_logging()
    return asyncio.run(run_beat_singleton(BEAT_SCAN_ADS, _scan_ads))


@celery_app.task(name="sync.scan_listing_diffs")
def scan_listing_diffs() -> dict[str, Any]:
    """每日找出有已关联 Listing 的租户，再按租户入队。"""

    configure_logging()
    return asyncio.run(run_beat_singleton(BEAT_SCAN_LISTING_DIFFS, _scan_listing_diffs))


async def _park_webhook(*, platform: str, body: str, event_id: str, kind: str, error: str) -> None:
    from app.services.webhook_ingress import dead_letter_for_task
    from app.sync_engine.runtime import get_dead_letter_queue

    letter = dead_letter_for_task(
        platform=platform,
        body=body,
        event_id=event_id,
        kind=kind,
        error=error,
        tenant_id=0,
    )
    await get_dead_letter_queue().push(letter)
