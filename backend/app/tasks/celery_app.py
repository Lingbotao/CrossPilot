"""Celery 应用（同步引擎、批量操作、报表导出的执行底座）。

## ⚠️ 最重要的一条约定：Celery 不继承 contextvar

Web 请求里的租户上下文存在 ``contextvars`` 里，而 Celery 任务是**另一个进程/另一个任务**
执行的 —— 上下文不会跟过去。如果任务里直接查库，
ORM 会因为"缺少租户上下文"直接报错（这是我们刻意设计的 fail-closed）。

**因此每个任务必须显式声明 tenant_id，并用 ``tenant_context`` 包裹::

    @celery_app.task(name="sync.orders")
    def sync_orders(shop_id: int, tenant_id: int) -> None:
        with tenant_context(tenant_id):          # ← 必须
            asyncio.run(_do_sync(shop_id, tenant_id))

同步 ``def`` 任务 + ``asyncio.run`` 是当前阶段的取舍：数据库层是异步的，
而 Celery 原生不支持 async 任务。任务量大之后再引入专用 event loop 复用方案。
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.logging import configure_logging

celery_app = Celery(
    "crosspilot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.sync", "app.tasks.batch", "app.tasks.report"],
)

celery_app.conf.update(
    # ---- 序列化：只允许 JSON，pickle 有反序列化任意代码执行风险 ----
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # ---- 时间：全系统 UTC，避免跨时区调度错乱 ----
    timezone="UTC",
    enable_utc=True,
    # ---- 可靠性：任务在 worker 崩溃后能被重新投递 ----
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # 长任务场景：不要预取，避免单 worker 囤积任务
    # ---- 限流与超时 ----
    task_time_limit=600,  # 硬超时 10min：平台调用不该超过这个时间
    task_soft_time_limit=540,
    broker_connection_retry_on_startup=True,
    result_expires=3600 * 24,
    # ---- 路由：同步任务与报表任务分开队列，避免大批量导出把同步饿死 ----
    task_routes={
        "sync.*": {"queue": "sync"},
        "batch.*": {"queue": "batch"},
        "report.*": {"queue": "report"},
    },
    task_default_queue="sync",
)

# 定时任务（M2/M5 逐步启用）
celery_app.conf.beat_schedule = {
    # 订单增量同步：各平台独立频率，具体值走 platform 配置表（约束 C5，不在这里写死）
    "beat-scan-sync-due": {
        "task": "sync.scan_due_shops",
        "schedule": crontab(minute="*/5"),
    },
    # 平台凭证续期巡检：令牌快过期前刷新，避免同步中断
    "beat-refresh-credentials": {
        "task": "sync.refresh_expiring_credentials",
        "schedule": crontab(minute="*/30"),
    },
    # 死信队列巡检：有任务进 DLQ 就告警（M2 落地）
    "beat-dead-letter-scan": {
        "task": "sync.scan_dead_letter",
        "schedule": crontab(minute="*/10"),
    },
}


@celery_app.task(name="system.healthcheck")
def healthcheck() -> dict[str, Any]:
    """连通性自检：``celery -A app.tasks.celery_app:celery_app call system.healthcheck``。"""
    configure_logging()
    return {"status": "ok", "env": settings.app_env}
