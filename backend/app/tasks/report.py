"""报表导出任务。

导出文件写对象存储，下载必须再过一遍租户和权限校验，不给静态公网地址。
5 万行要在 60 秒内写完，所以按页读取、按行落盘，不把整表装进内存。
"""

from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from app.core.config import settings
from app.core.context import tenant_context
from app.core.logging import configure_logging, get_logger
from app.db.tenant_filter import SKIP_FLAG
from app.engines.report import EXPORT_EXPIRED
from app.sync_engine.locks import BEAT_EXPIRE_EXPORTS
from app.sync_engine.runtime import run_beat_singleton
from app.tasks.celery_app import celery_app

log = get_logger(__name__)
_INLINE: set[asyncio.Task[None]] = set()


@celery_app.task(name="report.run_export")
def run_export(tenant_id: int, export_id: int) -> dict[str, Any]:
    """生成一个租户的导出文件。必须显式传入 tenant_id。"""

    configure_logging()
    with tenant_context(tenant_id):
        return asyncio.run(_run_export(tenant_id, export_id))


def _broker_open() -> bool:
    parsed = urlparse(settings.celery_broker_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


async def schedule_export(tenant_id: int, export_id: int) -> None:
    """有队列就入队。本机没起 worker 时在 API 进程里生成，避免任务一直停在排队。"""

    if _broker_open():
        try:
            run_export.delay(tenant_id, export_id)
            return
        except Exception:
            log.exception("report_export_enqueue_failed", export_id=export_id)
    log.info("report_export_inline", export_id=export_id, tenant_id=tenant_id)
    task = asyncio.create_task(_run_export_guarded(tenant_id, export_id))
    _INLINE.add(task)
    task.add_done_callback(_INLINE.discard)


async def _run_export_guarded(tenant_id: int, export_id: int) -> None:
    try:
        with tenant_context(tenant_id):
            await _run_export(tenant_id, export_id)
    except Exception:
        log.exception("report_export_inline_failed", export_id=export_id, tenant_id=tenant_id)


async def _run_export(tenant_id: int, export_id: int) -> dict[str, Any]:
    from app.db.session import session_scope
    from app.services.report import ReportService

    async with session_scope(tenant_id) as session:
        view = await ReportService(session).execute(export_id)
    return view.model_dump(mode="json")


@celery_app.task(name="report.expire_exports")
def expire_exports() -> dict[str, Any]:
    """清理已过保留期的导出文件。扫描本身跳过租户过滤，更新时进入每个租户。"""

    configure_logging()
    return asyncio.run(run_beat_singleton(BEAT_EXPIRE_EXPORTS, _expire_exports))


async def _expire_exports() -> dict[str, Any]:
    from app.db.session import owner_session_scope, session_scope
    from app.models.report import ReportExport
    from app.services.report import ReportService

    async with owner_session_scope() as session:
        stmt = (
            select(ReportExport.id, ReportExport.tenant_id)
            .where(ReportExport.expires_at <= datetime.now(UTC), ReportExport.status != EXPORT_EXPIRED)
            .execution_options(**{SKIP_FLAG: True})
        )
        due = list((await session.execute(stmt)).all())
    grouped: dict[int, list[int]] = {}
    for export_id, tenant_id in due:
        grouped.setdefault(int(tenant_id), []).append(int(export_id))
    expired = 0
    for tenant_id, export_ids in grouped.items():
        with tenant_context(tenant_id):
            async with session_scope(tenant_id) as session:
                service = ReportService(session)
                for export_id in export_ids:
                    await service.expire(export_id)
                    expired += 1
    log.info("report_exports_expired", count=expired)
    return {"expired": expired}


__all__ = ["expire_exports", "run_export", "schedule_export"]
