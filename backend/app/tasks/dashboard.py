"""看板预聚合任务。扫描跳过租户过滤，重建时显式进入每个租户。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.context import tenant_context
from app.core.logging import configure_logging, get_logger
from app.sync_engine.locks import BEAT_SCAN_DASHBOARD
from app.sync_engine.runtime import run_beat_singleton
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="dashboard.rebuild_tenant")
def rebuild_tenant(tenant_id: int, book_currency: str = "CNY") -> dict[str, Any]:
    """重建一个租户的看板汇总。必须显式传入 tenant_id。"""
    configure_logging()
    with tenant_context(tenant_id):
        return asyncio.run(_rebuild_tenant(tenant_id, book_currency))


async def _rebuild_tenant(tenant_id: int, book_currency: str) -> dict[str, Any]:
    from app.db.session import session_scope
    from app.services.dashboard import DashboardService

    end = datetime.now(UTC).date()
    start = end - timedelta(days=settings.dashboard_lookback_days - 1)
    async with session_scope(tenant_id) as session:
        view = await DashboardService(session).rebuild(
            date_from=start,
            date_to=end,
            book_currency=book_currency,
        )
    return view.model_dump(mode="json")


async def _scan_dashboard() -> dict[str, Any]:
    from sqlalchemy import select

    from app.db.session import owner_session_scope
    from app.models.tenant import Tenant
    from app.repositories.platform import ShopRepository

    async with owner_session_scope() as session:
        shops = await ShopRepository(session).list_active_across_tenants()
        tenant_ids = sorted({tenant_id for tenant_id, _shop_id, _platform in shops})
        if not tenant_ids:
            return {"tenants": 0}
        stmt = select(Tenant.id, Tenant.default_currency).where(Tenant.id.in_(tenant_ids))
        rows = (await session.execute(stmt)).all()
    for tenant_id, currency in rows:
        rebuild_tenant.delay(int(tenant_id), str(currency).strip() or "CNY")
    log.info("dashboard_scan_enqueued", tenants=len(rows))
    return {"tenants": len(rows)}


@celery_app.task(name="dashboard.scan_rebuild")
def scan_rebuild() -> dict[str, Any]:
    """按配置间隔为仍有授权店铺的租户入队看板重建。"""
    configure_logging()
    return asyncio.run(run_beat_singleton(BEAT_SCAN_DASHBOARD, _scan_dashboard))
