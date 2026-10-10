"""客服消息同步。扫描跳过租户过滤，拉取时显式进入每个租户。"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.context import tenant_context
from app.core.logging import configure_logging, get_logger
from app.sync_engine.locks import BEAT_SCAN_CS
from app.sync_engine.runtime import run_beat_singleton
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="cs.sync_shop_messages")
def sync_shop_messages(shop_id: int, tenant_id: int, trigger: str = "beat") -> dict[str, Any]:
    """拉取一个店铺的买家消息。必须显式传入 tenant_id。不在站内回复。"""
    configure_logging()
    with tenant_context(tenant_id):
        return asyncio.run(_sync_shop_messages(shop_id, tenant_id, trigger))


async def _sync_shop_messages(shop_id: int, tenant_id: int, trigger: str) -> dict[str, Any]:
    from app.db.session import session_scope
    from app.models.enums import SyncTrigger
    from app.schemas.cs import CsSyncRequest
    from app.services.cs import CsService

    kind = SyncTrigger.MANUAL if trigger == "manual" else SyncTrigger.SCHEDULED
    async with session_scope(tenant_id) as session:
        view = await CsService(session).sync(
            CsSyncRequest(shop_id=str(shop_id)),
            tenant_id=tenant_id,
            actor_id=None,
            idempotency_key=None,
            trigger=kind,
        )
    return view.model_dump()


async def _scan_messages() -> dict[str, Any]:
    from app.db.session import owner_session_scope
    from app.repositories.platform import ShopRepository

    async with owner_session_scope() as session:
        shops = await ShopRepository(session).list_active_across_tenants()
    for tenant_id, shop_id, _platform in shops:
        sync_shop_messages.delay(shop_id, tenant_id, trigger="beat")
    log.info("cs_scan_enqueued", shops=len(shops))
    return {"shops": len(shops)}


@celery_app.task(name="cs.scan_messages")
def scan_messages() -> dict[str, Any]:
    """按配置间隔找出仍在授权中的店铺，再按租户入队消息拉取。"""
    configure_logging()
    return asyncio.run(run_beat_singleton(BEAT_SCAN_CS, _scan_messages))
