"""同步任务（M2 落地）。

本文件在 M0 阶段只提供**任务骨架与租户上下文范式**，真正的同步引擎在 M2 实现。
保留骨架的价值：让"跨租户定时任务"与"租户内任务"两种范式在脚手架阶段就定型，
避免 M2 写任务时把租户上下文搞错（那是数据串租户的高发地）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.context import tenant_context
from app.core.logging import configure_logging, get_logger
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# 范式 A：租户内任务 —— 必须显式传 tenant_id 并用 tenant_context 包裹
# ---------------------------------------------------------------------------
@celery_app.task(name="sync.shop_orders", bind=True, max_retries=3)
def sync_shop_orders(self: Any, shop_id: int, tenant_id: int, trigger: str = "beat") -> dict[str, Any]:
    """同步单个店铺的订单。

    ``tenant_id`` 是**必填参数**，不是从上下文里"猜"的 ——
    Celery 任务不继承请求上下文，猜不出来。缺了它 ORM 会直接拒绝查询（fail-closed）。
    """
    configure_logging()
    with tenant_context(tenant_id):
        log.info("sync_shop_orders_started", shop_id=shop_id, trigger=trigger)
        # M2 实现：adapter_registry.get(platform).fetch_orders(...) → 幂等 UPSERT
        return {"shop_id": shop_id, "status": "not_implemented", "until": "M2"}


# ---------------------------------------------------------------------------
# 范式 B：平台级任务 —— 需要跨租户扫描，必须显式声明跳过租户过滤
# ---------------------------------------------------------------------------
async def _scan_due_shops_impl() -> dict[str, Any]:
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.db.tenant_filter import SKIP_FLAG

    async with session_scope() as session:
        # 这里刻意跨租户：定时任务要扫描"所有到点该同步的店铺"。
        # 显式带 SKIP_FLAG，ORM 会打一条 warning 日志留痕 —— 便于审计"谁绕过了隔离"。
        stmt = select(1).execution_options(**{SKIP_FLAG: True})
        await session.execute(stmt)
    return {"scanned": 0, "status": "not_implemented", "until": "M2"}


@celery_app.task(name="sync.scan_due_shops")
def scan_due_shops() -> dict[str, Any]:
    """扫描到点该同步的店铺并入队（Beat 每 5 分钟触发）。"""
    configure_logging()
    return asyncio.run(_scan_due_shops_impl())


@celery_app.task(name="sync.refresh_expiring_credentials")
def refresh_expiring_credentials() -> dict[str, Any]:
    """令牌续期巡检：快过期的刷新、已过期的标记店铺需重新授权（M2 落地）。"""
    configure_logging()
    log.info("refresh_expiring_credentials_tick")
    return {"refreshed": 0, "status": "not_implemented", "until": "M2"}


@celery_app.task(name="sync.scan_dead_letter")
def scan_dead_letter() -> dict[str, Any]:
    """死信队列巡检：有任务进 DLQ 即告警（PRD R3，M2 落地）。"""
    configure_logging()
    log.info("scan_dead_letter_tick")
    return {"dead_letters": 0, "status": "not_implemented", "until": "M2"}
