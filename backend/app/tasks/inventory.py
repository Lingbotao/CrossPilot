"""库存回传任务。必须显式传入 tenant_id。滞后降 0 由 Beat 按租户分发。"""

from __future__ import annotations

import asyncio
from typing import Any

from celery.exceptions import MaxRetriesExceededError

from app.adapters.errors import RetryDecision
from app.core.context import tenant_context
from app.core.logging import configure_logging, get_logger
from app.sync_engine.resilience import RetryAction, make_dead_letter, plan_retry
from app.sync_engine.runtime import get_dead_letter_queue, get_policy, run_beat_singleton
from app.tasks.celery_app import celery_app

log = get_logger(__name__)
_BEAT_SWEEP = "beat:inventory_lag_sweep"


@celery_app.task(name="batch.push_inventory", bind=True, max_retries=3)
def push_inventory(self: Any, tenant_id: int, sku_id: int, retry_count: int = 0) -> dict[str, Any]:
    configure_logging()
    with tenant_context(tenant_id):
        outcome = asyncio.run(_push(tenant_id, sku_id, retry_count=retry_count or int(self.request.retries or 0)))
    if outcome["retry"]:
        _retry_or_dead(
            self,
            tenant_id=tenant_id,
            sku_id=sku_id,
            error=str(outcome["error"]),
            decision=str(outcome["decision"]),
            retry_after_seconds=outcome["retry_after_seconds"],
        )
    return outcome


@celery_app.task(name="batch.sweep_inventory_lag")
def sweep_inventory_lag(tenant_id: int) -> dict[str, Any]:
    configure_logging()
    with tenant_context(tenant_id):
        return asyncio.run(_sweep(tenant_id))


@celery_app.task(name="batch.scan_inventory_lag")
def scan_inventory_lag() -> dict[str, Any]:
    configure_logging()
    return asyncio.run(run_beat_singleton(_BEAT_SWEEP, _scan))


def enqueue_inventory_push(tenant_id: int, sku_id: int) -> None:
    push_inventory.delay(tenant_id, sku_id)


async def _push(tenant_id: int, sku_id: int, *, retry_count: int) -> dict[str, Any]:
    from app.db.session import session_scope
    from app.services.inventory import InventoryService

    async with session_scope(tenant_id) as session:
        run = await InventoryService(session).push_sku(sku_id, sweep=False, retry_count=retry_count)
    return {
        "logs": len(run.logs),
        "retry": run.retry,
        "error": run.error,
        "decision": run.decision.value,
        "retry_after_seconds": run.retry_after_seconds,
    }


async def _sweep(tenant_id: int) -> dict[str, Any]:
    from app.db.session import session_scope
    from app.services.inventory import InventoryService

    async with session_scope(tenant_id) as session:
        run = await InventoryService(session).sweep_lag()
    return {"logs": len(run.logs), "zeroed": run.zeroed, "retry": run.retry}


async def _scan() -> dict[str, Any]:
    from app.services.listing_diff import list_linked_tenants

    tenant_ids = await list_linked_tenants()
    for tenant_id in tenant_ids:
        sweep_inventory_lag.delay(tenant_id)
    return {"tenants": len(tenant_ids)}


def _retry_or_dead(
    task: Any,
    *,
    tenant_id: int,
    sku_id: int,
    error: str,
    decision: str,
    retry_after_seconds: float | None,
) -> None:
    try:
        parsed = RetryDecision(decision)
    except ValueError:
        parsed = RetryDecision.RETRY
    plan = plan_retry(
        parsed,
        retry_attempt=int(getattr(task.request, "retries", 0) or 0),
        refresh_attempted=False,
        policy=get_policy(),
        retry_after_seconds=retry_after_seconds,
    )
    letter = make_dead_letter(
        task_name="batch.push_inventory",
        tenant_id=tenant_id,
        kwargs={"tenant_id": tenant_id, "sku_id": sku_id},
        error=error,
    )

    async def _park() -> None:
        await get_dead_letter_queue().push(letter)

    if plan.action is RetryAction.DEAD_LETTER or plan.action is RetryAction.FAIL:
        asyncio.run(_park())
        return
    try:
        raise task.retry(countdown=plan.delay_seconds)
    except MaxRetriesExceededError:
        asyncio.run(_park())


__all__ = ["enqueue_inventory_push", "push_inventory", "scan_inventory_lag", "sweep_inventory_lag"]
