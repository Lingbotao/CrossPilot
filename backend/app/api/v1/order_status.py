"""平台状态映射与订单状态时间轴。"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbSession, Identity, require_permission
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.models.config import PlatformStatusMapping
from app.models.order import OrderStatusLog
from app.schemas.order_status import (
    OrderStatusLogResponse,
    StatusMappingResponse,
    StatusSourceCode,
    UpsertStatusMappingRequest,
)
from app.services.order_status import OrderStatusService

router = APIRouter(tags=["订单状态"])

OrderReader = Annotated[Identity, Depends(require_permission(Perm.ORDER_READ))]
MappingWriter = Annotated[Identity, Depends(require_permission(Perm.SYSTEM_WRITE))]


def _mapping(row: PlatformStatusMapping) -> StatusMappingResponse:
    return StatusMappingResponse(
        id=row.id,
        platform_code=row.platform_code,
        platform_status=row.platform_status,
        unified_status=row.unified_status,
        updated_at=row.updated_at,
    )


def _log(row: OrderStatusLog) -> OrderStatusLogResponse:
    return OrderStatusLogResponse(
        id=row.id,
        order_id=row.order_id,
        from_status=row.from_status,
        to_status=row.to_status,
        platform_status=row.platform_status,
        operator_id=row.operator_id,
        source=cast(StatusSourceCode, row.source),
        remark=row.remark,
        created_at=row.created_at,
    )


@router.get(
    "/order-status-mappings",
    response_model=ApiResponse[list[StatusMappingResponse]],
    summary="平台状态映射",
)
async def list_status_mappings(
    identity: OrderReader,
    session: DbSession,
    platform_code: Annotated[str | None, Query(max_length=32)] = None,
) -> ApiResponse[list[StatusMappingResponse]]:
    del identity
    rows = await OrderStatusService(session).list_mappings(platform_code)
    return ok([_mapping(row) for row in rows])


@router.put(
    "/order-status-mappings",
    response_model=ApiResponse[StatusMappingResponse],
    summary="新增或覆盖一条平台状态映射",
)
async def upsert_status_mapping(
    payload: UpsertStatusMappingRequest,
    identity: MappingWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[StatusMappingResponse]:
    row = await OrderStatusService(session).save_mapping(
        platform_code=payload.platform_code,
        platform_status=payload.platform_status,
        unified_status=payload.unified_status,
        user_id=identity.user.id,
    )
    return ok(_mapping(row))


@router.get(
    "/orders/{order_id}/status-logs",
    response_model=ApiResponse[list[OrderStatusLogResponse]],
    summary="订单状态时间轴",
)
async def list_order_status_logs(
    order_id: int,
    identity: OrderReader,
    session: DbSession,
) -> ApiResponse[list[OrderStatusLogResponse]]:
    del identity
    rows = await OrderStatusService(session).list_logs(order_id)
    return ok([_log(row) for row in rows])


__all__ = ["router"]
