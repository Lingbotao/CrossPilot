"""批量操作任务。

M2 的批量发货走 ``POST /orders/batch-ship``（请求内逐单保存点）。
M3 的刊登和改价走本模块：任务名以 ``batch.`` 开头，进入 batch 队列。
必须显式传入 ``tenant_id``。异步任务中心留到 M6。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.context import tenant_context
from app.core.logging import configure_logging
from app.tasks.celery_app import celery_app

__all__ = ["celery_app", "enqueue_prices", "enqueue_publish"]


@celery_app.task(name="batch.placeholder")
def placeholder() -> dict[str, Any]:
    """占位。M2 批量发货走订单接口；异步任务中心的进度轮询留到 M6。"""
    return {"status": "not_implemented", "until": "M6"}


@celery_app.task(name="batch.publish_listings")
def publish_listings(batch_id: int, tenant_id: int) -> dict[str, Any]:
    configure_logging()
    with tenant_context(tenant_id):
        return asyncio.run(_run_publish(batch_id, tenant_id))


@celery_app.task(name="batch.update_prices")
def update_prices(batch_id: int, tenant_id: int) -> dict[str, Any]:
    configure_logging()
    with tenant_context(tenant_id):
        return asyncio.run(_run_prices(batch_id, tenant_id))


def enqueue_publish(batch_id: int, tenant_id: int) -> None:
    publish_listings.delay(batch_id, tenant_id)


def enqueue_prices(batch_id: int, tenant_id: int) -> None:
    update_prices.delay(batch_id, tenant_id)


async def _run_publish(batch_id: int, tenant_id: int) -> dict[str, Any]:
    from app.db.session import session_scope
    from app.services.listing_batch import ListingBatchService

    async with session_scope(tenant_id) as session:
        view = await ListingBatchService(session).run_publish(batch_id)
    return {"id": str(view.id), "status": view.status}


async def _run_prices(batch_id: int, tenant_id: int) -> dict[str, Any]:
    from app.db.session import session_scope
    from app.services.listing_batch import ListingBatchService

    async with session_scope(tenant_id) as session:
        view = await ListingBatchService(session).run_prices(batch_id)
    return {"id": str(view.id), "status": view.status}
