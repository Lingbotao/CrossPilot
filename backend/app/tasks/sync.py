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
from app.sync_engine.locks import BEAT_REFRESH_CREDENTIALS, BEAT_SCAN_DEAD_LETTER, BEAT_SCAN_DUE_SHOPS
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
