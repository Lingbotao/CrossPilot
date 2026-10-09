"""SKU 日利润重算。必须显式传入 tenant_id，不按全库静默扫描。"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from app.core.context import tenant_context
from app.core.logging import configure_logging
from app.tasks.celery_app import celery_app


@celery_app.task(name="finance.materialize_sku_profit")
def materialize_sku_profit(tenant_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    configure_logging()
    with tenant_context(tenant_id):
        return asyncio.run(_materialize(tenant_id, payload))


async def _materialize(tenant_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import session_scope
    from app.schemas.profit import MaterializeRequest
    from app.services.profit import ProfitService

    request = MaterializeRequest.model_validate(_dates(payload))
    async with session_scope(tenant_id) as session:
        view = await ProfitService(session).materialize(request, tenant_id=tenant_id, actor_id=0)
    return view.model_dump(mode="json")


def _dates(payload: dict[str, Any]) -> dict[str, Any]:
    copied = dict(payload)
    for key in ("date_from", "date_to"):
        value = copied.get(key)
        if isinstance(value, str):
            copied[key] = date.fromisoformat(value)
    return copied
