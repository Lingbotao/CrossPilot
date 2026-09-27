"""订单列表、详情、导出、批量发货与面单。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbSession, Identity, require_permission
from app.core.errors import AppError, ErrorCode, ParamInvalidError
from app.core.pagination import MAX_PAGE_SIZE, PageData
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.order import (
    BatchShipRequest,
    BatchShipResult,
    FilePayload,
    LabelRequest,
    OrderDetail,
    OrderListItem,
    parse_order_id,
)
from app.services.order_query import OrderQueryService, parse_amount, parse_optional_id
from app.services.order_ship import OrderShipService

router = APIRouter(prefix="/orders", tags=["订单"])

OrderReader = Annotated[Identity, Depends(require_permission(Perm.ORDER_READ))]
OrderShipper = Annotated[Identity, Depends(require_permission(Perm.ORDER_SHIP))]


@router.get("", response_model=ApiResponse[PageData[OrderListItem]], summary="订单列表")
async def list_orders(
    identity: OrderReader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    platform_code: Annotated[str | None, Query(max_length=32)] = None,
    shop_id: Annotated[str | None, Query(max_length=32)] = None,
    site_code: Annotated[str | None, Query(max_length=8)] = None,
    unified_status: Annotated[str | None, Query(max_length=32)] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    amount_min: Annotated[str | None, Query(max_length=32)] = None,
    amount_max: Annotated[str | None, Query(max_length=32)] = None,
    sku: Annotated[str | None, Query(max_length=128)] = None,
    q: Annotated[str | None, Query(max_length=128)] = None,
) -> ApiResponse[PageData[OrderListItem]]:
    data = await OrderQueryService(session).list_orders(
        role_code=identity.role_code,
        limit=limit,
        cursor=cursor,
        platform_code=platform_code,
        shop_id=parse_optional_id(shop_id),
        site_code=site_code,
        unified_status=unified_status,
        created_from=created_from,
        created_to=created_to,
        amount_min=parse_amount(amount_min),
        amount_max=parse_amount(amount_max),
        sku=sku,
        keyword=q,
    )
    return ok(data)


@router.get("/export", response_model=ApiResponse[FilePayload], summary="导出订单")
async def export_orders(
    identity: OrderReader,
    session: DbSession,
    fields: Annotated[str | None, Query(max_length=500)] = None,
    platform_code: Annotated[str | None, Query(max_length=32)] = None,
    shop_id: Annotated[str | None, Query(max_length=32)] = None,
    site_code: Annotated[str | None, Query(max_length=8)] = None,
    unified_status: Annotated[str | None, Query(max_length=32)] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    amount_min: Annotated[str | None, Query(max_length=32)] = None,
    amount_max: Annotated[str | None, Query(max_length=32)] = None,
    sku: Annotated[str | None, Query(max_length=128)] = None,
    q: Annotated[str | None, Query(max_length=128)] = None,
) -> ApiResponse[FilePayload]:
    chosen = [item for item in (fields or "").split(",") if item.strip()] or None
    data = await OrderQueryService(session).export_orders(
        role_code=identity.role_code,
        user_id=identity.user.id,
        tenant_id=identity.tenant.id,
        fields=chosen,
        platform_code=platform_code,
        shop_id=parse_optional_id(shop_id),
        site_code=site_code,
        unified_status=unified_status,
        created_from=created_from,
        created_to=created_to,
        amount_min=parse_amount(amount_min),
        amount_max=parse_amount(amount_max),
        sku=sku,
        keyword=q,
    )
    return ok(data)


@router.get("/{order_id}", response_model=ApiResponse[OrderDetail], summary="订单详情")
async def get_order(
    order_id: str,
    identity: OrderReader,
    session: DbSession,
) -> ApiResponse[OrderDetail]:
    data = await OrderQueryService(session).get_order(_order_id(order_id), role_code=identity.role_code)
    return ok(data)


@router.post("/batch-ship", response_model=ApiResponse[BatchShipResult], summary="批量发货")
async def batch_ship(
    payload: BatchShipRequest,
    identity: OrderShipper,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[BatchShipResult]:
    result = await OrderShipService(session).batch_ship(
        order_ids=[_order_id(item) for item in payload.order_ids],
        carrier=payload.carrier,
        operator_id=identity.user.id,
        tenant_id=identity.tenant.id,
    )
    if result.failed:
        # 部分成功已经写在当前事务里。先提交再抛 50003，避免异常处理把成功单一起回滚。
        await session.commit()
        raise AppError(
            "部分订单发货失败，失败单可重试",
            code=ErrorCode.BATCH_SHIP_PARTIAL_FAILED,
            data=result.model_dump(mode="json"),
        )
    return ok(result)


@router.post("/labels", response_model=ApiResponse[FilePayload], summary="合并打印面单")
async def print_labels(
    payload: LabelRequest,
    identity: OrderShipper,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[FilePayload]:
    data = await OrderShipService(session).print_labels(
        order_ids=[_order_id(item) for item in payload.order_ids],
        size=payload.size,
        role_code=identity.role_code,
    )
    return ok(data)


def _order_id(value: str) -> int:
    try:
        return parse_order_id(value)
    except ValueError as exc:
        raise ParamInvalidError(str(exc)) from exc


__all__ = ["router"]
