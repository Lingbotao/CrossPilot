"""店铺订单增量同步（M2-05）。

手动触发和 Beat 走同一条入库路径，幂等键相同，所以两个通道只会留下一行。
没有显式时间范围时，窗口从高水位向前重叠；分页没拉完就记下平台游标，下次接着拉。
显式补拉只入库，不移动高水位。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import CredentialView, PageResult, UnifiedOrder
from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError, RetryDecision
from app.adapters.registry import adapter_registry
from app.core.context import get_trace_id
from app.core.errors import ParamInvalidError
from app.core.logging import get_logger
from app.db.session import owner_session_scope
from app.models.enums import ShopStatus, SyncStatus, SyncTrigger
from app.models.platform import PlatformApiLog, Shop, SyncTask
from app.repositories.order import SalesOrderRepository, ShopSyncCursorRepository
from app.repositories.platform import (
    PlatformApiLogRepository,
    ShopCredentialRepository,
    ShopRepository,
    SyncTaskRepository,
)
from app.schemas.common import money_to_str
from app.services.credential_service import view_from_row
from app.sync_engine.cursor import (
    OrderSyncWindow,
    PullResult,
    checkpoint_for,
    order_sync_window,
    pull_order_pages,
    shop_sync_message,
)
from app.sync_engine.errors import StoreUnavailable
from app.sync_engine.idempotency import WriteAction, dedup_hint, order_idempotency_key, remember
from app.sync_engine.resilience import counts_as_outage
from app.sync_engine.runtime import get_bloom, get_circuit_breaker, get_policy, get_rate_limiter

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SyncRun:
    payload: dict[str, object]
    task_id: int | None = None
    decision: RetryDecision | None = None
    retry_after_seconds: float | None = None


class OrderSyncService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = SalesOrderRepository(session)
        self.cursors = ShopSyncCursorRepository(session)
        self.shops = ShopRepository(session)
        self.credentials = ShopCredentialRepository(session)
        self.tasks = SyncTaskRepository(session)
        self.api_logs = PlatformApiLogRepository(session)
        register_builtin_adapters()

    async def run(
        self,
        shop: Shop,
        *,
        trigger: SyncTrigger,
        since: datetime | None,
        until: datetime | None,
    ) -> SyncRun:
        if shop.status != int(ShopStatus.ACTIVE):
            return SyncRun(payload={"shop_id": shop.id, "status": "skipped", "reason": "shop_inactive"})
        prepared = await self._window(shop, since=since, until=until)
        now = datetime.now(UTC)
        task = await self.tasks.add(
            SyncTask(
                tenant_id=shop.tenant_id,
                shop_id=shop.id,
                module="order",
                trigger_type=int(trigger),
                status=int(SyncStatus.RUNNING),
                started_at=now,
                since=prepared.since,
                until=prepared.until,
                stats={},
            )
        )
        cred = await self.credentials.get_by_shop_id(shop.id)
        if cred is None:
            return await self._fail(task, shop, "店铺凭证不存在", decision=RetryDecision.FAIL_FAST)
        view = view_from_row(shop, cred)
        started = time.perf_counter()
        batch = await self._fetch(
            shop, view, since=prepared.since, until=prepared.until, start_cursor=prepared.page_cursor
        )
        await self._write_api_log(shop, batch=batch, latency_ms=int((time.perf_counter() - started) * 1000))
        counts = await self._ingest(shop, batch.orders)
        stats: dict[str, object] = {
            **counts,
            "pulled": len(batch.orders),
            "pages": batch.pages,
            "first_order": _preview(batch.orders[0]) if batch.orders else None,
        }
        if prepared.advance and batch.error is None:
            point = checkpoint_for(
                complete=batch.complete,
                errored=False,
                failed=counts["failed"],
                until=prepared.until,
                resume_cursor=batch.resume_cursor,
                since=prepared.since,
            )
            if point is not None:
                await self.cursors.save(shop, point)
        finished = datetime.now(UTC)
        if batch.error is not None:
            task.status = int(SyncStatus.FAILED)
            task.error = str(batch.error)[:512]
            shop.last_sync_status = int(SyncStatus.FAILED)
            shop.last_error = task.error
            decision = batch.error.decision if isinstance(batch.error, AdapterError) else RetryDecision.RETRY
            retry_after = batch.error.retry_after_seconds if isinstance(batch.error, AdapterError) else None
        elif counts["failed"]:
            task.status = int(SyncStatus.FAILED)
            task.error = f"{counts['failed']} 笔订单没有写入"
            shop.last_sync_status = int(SyncStatus.FAILED)
            shop.last_error = task.error
            decision = None
            retry_after = None
        else:
            task.status = int(SyncStatus.SUCCESS)
            task.error = None
            shop.last_sync_status = int(SyncStatus.SUCCESS)
            shop.last_error = None
            decision = None
            retry_after = None
        task.finished_at = finished
        task.stats = stats
        shop.last_sync_at = finished
        await self.session.flush()
        return SyncRun(
            payload={
                "shop_id": shop.id,
                "task_id": task.id,
                "status": task.status,
                "stats": stats,
                "error": task.error,
            },
            task_id=task.id,
            decision=decision,
            retry_after_seconds=float(retry_after) if retry_after is not None else None,
        )

    async def _window(self, shop: Shop, *, since: datetime | None, until: datetime | None) -> OrderSyncWindow:
        policy = get_policy()
        overlap = timedelta(seconds=policy.order_overlap_seconds)
        lookback = timedelta(seconds=policy.order_initial_lookback_seconds)
        explicit = since is not None or until is not None
        if not explicit:
            stored = await self.cursors.get_order_cursor(shop.id)
            if stored is not None and stored.resume_cursor and stored.resume_since and stored.resume_until:
                return OrderSyncWindow(
                    since=stored.resume_since,
                    until=stored.resume_until,
                    page_cursor=stored.resume_cursor,
                    advance=True,
                )
            start, end = order_sync_window(
                cursor_at=None if stored is None else stored.cursor_at,
                now=datetime.now(UTC),
                overlap=overlap,
                initial_lookback=lookback,
            )
            return OrderSyncWindow(since=start, until=end, page_cursor=None, advance=True)
        end = until or datetime.now(UTC)
        start = since or (end - lookback)
        if start >= end:
            raise ParamInvalidError("同步开始时间必须早于结束时间")
        return OrderSyncWindow(since=start, until=end, page_cursor=None, advance=False)

    async def _fetch(
        self,
        shop: Shop,
        view: CredentialView,
        *,
        since: datetime,
        until: datetime,
        start_cursor: str | None,
    ) -> PullResult:
        policy = get_policy()
        platform = shop.platform_code
        breaker = get_circuit_breaker()
        try:
            allowed = await breaker.allow(platform)
        except StoreUnavailable:
            log.warning("order_sync_breaker_unavailable", platform=platform, shop_id=shop.id)
            return PullResult(
                orders=[],
                complete=False,
                resume_cursor=None,
                pages=0,
                error=AdapterError("熔断状态不可用", platform=platform, decision=RetryDecision.RETRY),
            )
        if not allowed:
            return PullResult(
                orders=[],
                complete=False,
                resume_cursor=None,
                pages=0,
                error=AdapterError("平台熔断中，本轮跳过", platform=platform, decision=RetryDecision.FAIL_FAST),
            )
        adapter = adapter_registry.get(platform)
        limiter = get_rate_limiter()
        spec = adapter.rate_limit()

        async def fetch(cursor: str | None) -> PageResult[UnifiedOrder]:
            try:
                slot = await limiter.try_acquire(platform, str(shop.id), spec)
            except StoreUnavailable:
                raise AdapterError("限流状态不可用", platform=platform, decision=RetryDecision.RETRY) from None
            if not slot.allowed:
                raise AdapterError(
                    "触发平台限流",
                    platform=platform,
                    http_status=429,
                    decision=RetryDecision.RETRY_AFTER,
                    retry_after_seconds=max(1, int(slot.retry_after_seconds)),
                )
            try:
                page = await adapter.fetch_orders(view, since=since, until=until, cursor=cursor)
            except AdapterError as exc:
                if exc.http_status == 429 or exc.decision is RetryDecision.RETRY_AFTER:
                    await limiter.record_throttled(platform, str(shop.id), spec)
                elif counts_as_outage(exc.decision):
                    await breaker.record(platform, failed=True)
                raise
            await limiter.record_success(platform, str(shop.id), spec)
            await breaker.record(platform, failed=False)
            return page

        return await pull_order_pages(fetch, start_cursor=start_cursor, max_pages=policy.order_max_pages)

    async def _ingest(self, shop: Shop, orders: list[UnifiedOrder]) -> dict[str, int]:
        inserted = updated = skipped = failed = 0
        bloom = get_bloom()
        for order in orders:
            key = order_idempotency_key(shop.platform_code, shop.id, order.platform_order_id)
            try:
                await dedup_hint(bloom, key)
            except StoreUnavailable:
                log.warning("order_bloom_unavailable", shop_id=shop.id)
            try:
                action = await self.orders.upsert(shop, order)
            except Exception:
                log.exception("order_ingest_failed", shop_id=shop.id, platform_order_id=order.platform_order_id)
                failed += 1
                continue
            if action is WriteAction.INSERT:
                inserted += 1
            elif action is WriteAction.UPDATE:
                updated += 1
            else:
                skipped += 1
            if action is not WriteAction.SKIP:
                try:
                    await remember(bloom, key)
                except StoreUnavailable:
                    log.warning("order_bloom_unavailable", shop_id=shop.id)
        return {"inserted": inserted, "updated": updated, "skipped": skipped, "failed": failed}

    async def _fail(
        self,
        task: SyncTask,
        shop: Shop,
        message: str,
        *,
        decision: RetryDecision,
    ) -> SyncRun:
        finished = datetime.now(UTC)
        task.status = int(SyncStatus.FAILED)
        task.finished_at = finished
        task.error = message[:512]
        task.stats = {
            "pulled": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "pages": 0,
            "first_order": None,
        }
        shop.last_sync_at = finished
        shop.last_sync_status = int(SyncStatus.FAILED)
        shop.last_error = task.error
        await self.session.flush()
        return SyncRun(
            payload={
                "shop_id": shop.id,
                "task_id": task.id,
                "status": task.status,
                "stats": task.stats,
                "error": task.error,
            },
            task_id=task.id,
            decision=decision,
        )

    async def _write_api_log(self, shop: Shop, *, batch: PullResult, latency_ms: int) -> None:
        error = batch.error
        http_status = error.http_status if isinstance(error, AdapterError) else None
        error_code = None
        if isinstance(error, AdapterError):
            error_code = error.platform_code or error.decision.value
        await self.api_logs.add(
            PlatformApiLog(
                tenant_id=shop.tenant_id,
                shop_id=shop.id,
                platform_code=shop.platform_code,
                endpoint="order.fetch",
                http_status=http_status if error is not None else 200,
                latency_ms=latency_ms,
                retry_count=0,
                request_id=get_trace_id(),
                error_code=error_code,
            )
        )


async def enqueue_due_order_syncs() -> dict[str, object]:
    """扫描到点的店铺并入队。扫描走表 owner，单店同步再回到租户上下文。"""
    policy = get_policy()
    due_before = datetime.now(UTC) - timedelta(seconds=policy.order_interval_seconds)
    async with owner_session_scope() as session:
        result = await session.execute(
            text(
                """
                SELECT s.id, s.tenant_id, s.platform_code
                FROM shop AS s
                LEFT JOIN shop_sync_cursor AS c
                  ON c.shop_id = s.id
                 AND c.tenant_id = s.tenant_id
                 AND c.module = 'order'
                WHERE s.deleted_at IS NULL
                  AND s.status = :active
                  AND (
                        c.resume_cursor IS NOT NULL
                     OR c.cursor_at IS NULL
                     OR c.cursor_at <= :due_before
                  )
                """
            ),
            {"active": int(ShopStatus.ACTIVE), "due_before": due_before},
        )
        due = [(int(shop_id), int(tenant_id), str(platform)) for shop_id, tenant_id, platform in result.all()]
    for shop_id, tenant_id, platform in due:
        _publish(shop_sync_message(shop_id=shop_id, tenant_id=tenant_id, platform=platform, trigger="beat"))
    return {"scanned": len(due), "enqueued": len(due), "status": "ok"}


def _publish(payload: dict[str, object]) -> None:
    from app.tasks.sync import sync_shop_orders

    sync_shop_orders.apply_async(kwargs=payload)


def _preview(order: UnifiedOrder) -> dict[str, object]:
    return {
        "platform_order_id": order.platform_order_id,
        "unified_status": order.unified_status,
        "currency": order.currency,
        "total_amount": money_to_str(order.total_amount),
    }


__all__ = ["OrderSyncService", "SyncRun", "enqueue_due_order_syncs"]
