"""库存台账、仓库、流水、回传和补货建议。跨租户 ID 返回 404。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbSession, Identity, require_permission
from app.core.errors import AppError, ErrorCode
from app.core.pagination import MAX_PAGE_SIZE, PageData
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.models.inventory import InventoryPushLog
from app.schemas.inventory import (
    FlowView,
    InventoryAdjust,
    InventorySafeStockPatch,
    InventoryView,
    PushLogView,
    PushRequest,
    ReplenishmentView,
    SafetyStockView,
    SafetyStockWrite,
    WarehousePatch,
    WarehouseView,
    WarehouseWrite,
)
from app.schemas.order import FilePayload
from app.services.inventory import InventoryService

router = APIRouter(tags=["库存"])

Reader = Annotated[Identity, Depends(require_permission(Perm.INVENTORY_READ))]
Writer = Annotated[Identity, Depends(require_permission(Perm.INVENTORY_WRITE))]


@router.get("/warehouses", response_model=ApiResponse[PageData[WarehouseView]], summary="仓库列表")
async def list_warehouses(
    identity: Reader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> ApiResponse[PageData[WarehouseView]]:
    del identity
    data = await InventoryService(session).list_warehouses(limit=limit, cursor=cursor)
    return ok(data)


@router.post("/warehouses", response_model=ApiResponse[WarehouseView], summary="新建仓库")
async def create_warehouse(
    payload: WarehouseWrite,
    identity: Writer,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[WarehouseView]:
    data = await InventoryService(session).create_warehouse(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="仓库已创建")


@router.patch("/warehouses/{warehouse_id}", response_model=ApiResponse[WarehouseView], summary="修改仓库")
async def update_warehouse(
    warehouse_id: int,
    payload: WarehousePatch,
    identity: Writer,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[WarehouseView]:
    data = await InventoryService(session).update_warehouse(warehouse_id, payload, actor_id=identity.user.id)
    return ok(data, message="仓库已更新")


@router.delete("/warehouses/{warehouse_id}", response_model=ApiResponse[WarehouseView], summary="删除仓库")
async def delete_warehouse(
    warehouse_id: int,
    identity: Writer,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[WarehouseView]:
    data = await InventoryService(session).delete_warehouse(warehouse_id, actor_id=identity.user.id)
    return ok(data, message="仓库已删除")


@router.get("/inventories", response_model=ApiResponse[PageData[InventoryView]], summary="库存查询")
async def list_inventories(
    identity: Reader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    sku_id: Annotated[int | None, Query()] = None,
    warehouse_id: Annotated[int | None, Query()] = None,
) -> ApiResponse[PageData[InventoryView]]:
    del identity
    data = await InventoryService(session).list_inventories(
        limit=limit,
        cursor=cursor,
        sku_id=sku_id,
        warehouse_id=warehouse_id,
    )
    return ok(data)


@router.post("/inventories/adjust", response_model=ApiResponse[InventoryView], summary="库存调整")
async def adjust_inventory(
    payload: InventoryAdjust,
    identity: Writer,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[InventoryView]:
    data = await InventoryService(session).adjust(payload, tenant_id=identity.tenant.id, actor_id=identity.user.id)
    return ok(data, message="库存已调整")


@router.patch("/inventories/{inventory_id}", response_model=ApiResponse[InventoryView], summary="修改分仓安全水位")
async def patch_inventory_safety(
    inventory_id: int,
    payload: InventorySafeStockPatch,
    identity: Writer,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[InventoryView]:
    data = await InventoryService(session).set_safe_stock(inventory_id, payload.safe_stock, actor_id=identity.user.id)
    return ok(data, message="安全水位已更新")


@router.post("/inventories/push", response_model=ApiResponse[list[PushLogView]], summary="手动回传平台库存")
async def push_inventory(
    payload: PushRequest,
    identity: Writer,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[list[PushLogView]]:
    del identity
    run = await InventoryService(session).push_sku(payload.sku_id, sweep=False)
    views = [_push_log_view(row) for row in run.logs]
    if run.retry:
        await session.commit()
        raise AppError(run.error or "库存回传失败", code=ErrorCode.INVENTORY_SYNC_FAILED)
    return ok(views, message="库存已回传")


@router.get("/inventory-flows", response_model=ApiResponse[PageData[FlowView]], summary="库存流水")
async def list_flows(
    identity: Reader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    sku_id: Annotated[int | None, Query()] = None,
    warehouse_id: Annotated[int | None, Query()] = None,
    ref_type: Annotated[str | None, Query(max_length=32)] = None,
    ref_id: Annotated[int | None, Query()] = None,
    flow_type: Annotated[str | None, Query(max_length=16)] = None,
) -> ApiResponse[PageData[FlowView]]:
    del identity
    data = await InventoryService(session).list_flows(
        limit=limit,
        cursor=cursor,
        sku_id=sku_id,
        warehouse_id=warehouse_id,
        ref_type=ref_type,
        ref_id=ref_id,
        flow_type=flow_type,
    )
    return ok(data)


@router.get("/inventory-flows/export", response_model=ApiResponse[FilePayload], summary="导出库存流水")
async def export_flows(
    identity: Reader,
    session: DbSession,
    sku_id: Annotated[int | None, Query()] = None,
    warehouse_id: Annotated[int | None, Query()] = None,
    ref_type: Annotated[str | None, Query(max_length=32)] = None,
    ref_id: Annotated[int | None, Query()] = None,
) -> ApiResponse[FilePayload]:
    data = await InventoryService(session).export_flows(
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        sku_id=sku_id,
        warehouse_id=warehouse_id,
        ref_type=ref_type,
        ref_id=ref_id,
    )
    return ok(data)


@router.get("/inventory-push-logs", response_model=ApiResponse[PageData[PushLogView]], summary="库存回传日志")
async def list_push_logs(
    identity: Reader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    sku_id: Annotated[int | None, Query()] = None,
    shop_id: Annotated[int | None, Query()] = None,
) -> ApiResponse[PageData[PushLogView]]:
    del identity
    data = await InventoryService(session).list_push_logs(limit=limit, cursor=cursor, sku_id=sku_id, shop_id=shop_id)
    return ok(data)


@router.get("/platform-safety-stocks", response_model=ApiResponse[PageData[SafetyStockView]], summary="平台安全水位")
async def list_safety(
    identity: Reader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> ApiResponse[PageData[SafetyStockView]]:
    del identity
    data = await InventoryService(session).list_safety(limit=limit, cursor=cursor)
    return ok(data)


@router.put("/platform-safety-stocks", response_model=ApiResponse[SafetyStockView], summary="保存平台安全水位")
async def upsert_safety(
    payload: SafetyStockWrite,
    identity: Writer,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[SafetyStockView]:
    data = await InventoryService(session).upsert_safety(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="安全水位已保存")


@router.get("/inventory-replenishments", response_model=ApiResponse[list[ReplenishmentView]], summary="补货建议")
async def list_replenishments(
    identity: Reader,
    session: DbSession,
    window_days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> ApiResponse[list[ReplenishmentView]]:
    del identity
    data = await InventoryService(session).replenishments(window_days=window_days)
    return ok(data)


def _push_log_view(row: InventoryPushLog) -> PushLogView:
    return PushLogView(
        id=row.id,
        shop_id=row.shop_id,
        sku_id=row.sku_id,
        platform_code=row.platform_code,
        quantity=row.quantity,
        status=row.status,
        retry_count=row.retry_count,
        message=row.message,
        created_at=row.created_at,
    )


__all__ = ["router"]
