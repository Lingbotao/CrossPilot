"""供应商、采购单、在途和头程。跨租户 ID 返回 404。金额接口要求成本可见。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbSession, Identity, require_cost_visibility, require_permission
from app.core.pagination import MAX_PAGE_SIZE, PageData
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.purchase import (
    InTransitView,
    PurchaseOrderCreate,
    PurchaseOrderPatch,
    PurchaseOrderView,
    ReceiptCreate,
    ReceiptView,
    ReplenishmentConvert,
    ShipmentCreate,
    ShipmentView,
    SkuSupplierReplace,
    SupplierPatch,
    SupplierView,
    SupplierWrite,
)
from app.services.purchase import PurchaseService

suppliers = APIRouter(prefix="/suppliers", tags=["采购"])
orders = APIRouter(prefix="/purchase-orders", tags=["采购"])
transit = APIRouter(prefix="/purchase-in-transit", tags=["采购"])
shipments = APIRouter(prefix="/first-mile-shipments", tags=["采购"])

Reader = Annotated[Identity, Depends(require_permission(Perm.PURCHASE_READ))]
Writer = Annotated[Identity, Depends(require_permission(Perm.PURCHASE_WRITE))]


async def _cost_reader(identity: Reader) -> Identity:
    return await require_cost_visibility(identity)


async def _cost_writer(identity: Writer) -> Identity:
    return await require_cost_visibility(identity)


CostReader = Annotated[Identity, Depends(_cost_reader)]
CostWriter = Annotated[Identity, Depends(_cost_writer)]


@suppliers.get("", response_model=ApiResponse[PageData[SupplierView]], summary="供应商列表")
async def list_suppliers(
    identity: Reader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> ApiResponse[PageData[SupplierView]]:
    del identity
    data = await PurchaseService(session).list_suppliers(limit=limit, cursor=cursor)
    return ok(data)


@suppliers.post("", response_model=ApiResponse[SupplierView], summary="新建供应商")
async def create_supplier(
    payload: SupplierWrite,
    identity: Writer,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[SupplierView]:
    data = await PurchaseService(session).create_supplier(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        idempotency_key=idempotency_key,
    )
    return ok(data)


@suppliers.patch("/{supplier_id}", response_model=ApiResponse[SupplierView], summary="修改供应商")
async def update_supplier(
    supplier_id: int,
    payload: SupplierPatch,
    identity: Writer,
    session: DbSession,
) -> ApiResponse[SupplierView]:
    data = await PurchaseService(session).update_supplier(
        supplier_id,
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data)


@suppliers.delete("/{supplier_id}", response_model=ApiResponse[SupplierView], summary="删除供应商")
async def delete_supplier(
    supplier_id: int,
    identity: Writer,
    session: DbSession,
) -> ApiResponse[SupplierView]:
    data = await PurchaseService(session).delete_supplier(
        supplier_id,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data)


@suppliers.put("/{supplier_id}/skus", response_model=ApiResponse[SupplierView], summary="设置供应 SKU")
async def replace_supplier_skus(
    supplier_id: int,
    payload: SkuSupplierReplace,
    identity: Writer,
    session: DbSession,
) -> ApiResponse[SupplierView]:
    data = await PurchaseService(session).replace_links(
        supplier_id,
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data)


@orders.get("", response_model=ApiResponse[PageData[PurchaseOrderView]], summary="采购单列表")
async def list_orders(
    identity: CostReader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> ApiResponse[PageData[PurchaseOrderView]]:
    data = await PurchaseService(session).list_orders(limit=limit, cursor=cursor, visible=identity.can_view_cost)
    return ok(data)


@orders.post("", response_model=ApiResponse[PurchaseOrderView], summary="新建采购单")
async def create_order(
    payload: PurchaseOrderCreate,
    identity: CostWriter,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[PurchaseOrderView]:
    data = await PurchaseService(session).create_order(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        idempotency_key=idempotency_key,
        visible=identity.can_view_cost,
    )
    return ok(data)


@orders.post("/from-replenishment", response_model=ApiResponse[list[PurchaseOrderView]], summary="补货建议转采购草稿")
async def convert_replenishment(
    payload: ReplenishmentConvert,
    identity: CostWriter,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[list[PurchaseOrderView]]:
    data = await PurchaseService(session).from_replenishment(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        idempotency_key=idempotency_key,
        visible=identity.can_view_cost,
    )
    return ok(data)


@orders.patch("/{order_id}", response_model=ApiResponse[PurchaseOrderView], summary="变更采购单")
async def update_order(
    order_id: int,
    payload: PurchaseOrderPatch,
    identity: CostWriter,
    session: DbSession,
) -> ApiResponse[PurchaseOrderView]:
    data = await PurchaseService(session).update_order(
        order_id,
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        visible=identity.can_view_cost,
    )
    return ok(data)


@orders.post("/{order_id}/submit", response_model=ApiResponse[PurchaseOrderView], summary="提交采购单")
async def submit_order(
    order_id: int,
    identity: CostWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[PurchaseOrderView]:
    return await _transition(order_id, "submit", identity, session)


@orders.post("/{order_id}/approve", response_model=ApiResponse[PurchaseOrderView], summary="审核采购单")
async def approve_order(
    order_id: int,
    identity: CostWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[PurchaseOrderView]:
    return await _transition(order_id, "approve", identity, session)


@orders.post("/{order_id}/reject", response_model=ApiResponse[PurchaseOrderView], summary="驳回采购单")
async def reject_order(
    order_id: int,
    identity: CostWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[PurchaseOrderView]:
    return await _transition(order_id, "reject", identity, session)


@orders.post("/{order_id}/close", response_model=ApiResponse[PurchaseOrderView], summary="关闭采购单")
async def close_order(
    order_id: int,
    identity: CostWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[PurchaseOrderView]:
    return await _transition(order_id, "close", identity, session)


@orders.post("/{order_id}/cancel", response_model=ApiResponse[PurchaseOrderView], summary="取消采购单")
async def cancel_order(
    order_id: int,
    identity: CostWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[PurchaseOrderView]:
    return await _transition(order_id, "cancel", identity, session)


@orders.post("/{order_id}/receipts", response_model=ApiResponse[ReceiptView], summary="采购收货")
async def receive_order(
    order_id: int,
    payload: ReceiptCreate,
    identity: CostWriter,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ReceiptView]:
    data = await PurchaseService(session).receive(
        order_id,
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        idempotency_key=idempotency_key,
        visible=identity.can_view_cost,
    )
    return ok(data)


@transit.get("", response_model=ApiResponse[InTransitView], summary="在途库存")
async def list_in_transit(identity: Reader, session: DbSession) -> ApiResponse[InTransitView]:
    del identity
    return ok(await PurchaseService(session).in_transit())


@shipments.get("", response_model=ApiResponse[PageData[ShipmentView]], summary="头程物流单")
async def list_shipments(
    identity: CostReader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> ApiResponse[PageData[ShipmentView]]:
    data = await PurchaseService(session).list_shipments(limit=limit, cursor=cursor, visible=identity.can_view_cost)
    return ok(data)


@shipments.post("", response_model=ApiResponse[ShipmentView], summary="新建头程物流单")
async def create_shipment(
    payload: ShipmentCreate,
    identity: CostWriter,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ShipmentView]:
    data = await PurchaseService(session).create_shipment(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        idempotency_key=idempotency_key,
        visible=identity.can_view_cost,
    )
    return ok(data)


@shipments.post("/{shipment_id}/post", response_model=ApiResponse[ShipmentView], summary="头程过账并回写成本")
async def post_shipment(
    shipment_id: int,
    identity: CostWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ShipmentView]:
    data = await PurchaseService(session).post_shipment(
        shipment_id,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        visible=identity.can_view_cost,
    )
    return ok(data)


async def _transition(
    order_id: int,
    action: str,
    identity: Identity,
    session: DbSession,
) -> ApiResponse[PurchaseOrderView]:
    data = await PurchaseService(session).transition(
        order_id,
        action,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        visible=identity.can_view_cost,
    )
    return ok(data)
