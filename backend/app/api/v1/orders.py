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
from app.models.order import ReturnOrder
from app.schemas.order import (
    AddressLogView,
    AddressView,
    AddressWrite,
    BatchShipRequest,
    BatchShipResult,
    FilePayload,
    FreshnessView,
    LabelRequest,
    NoteView,
    NoteWrite,
    OrderDetail,
    OrderListItem,
    ReturnView,
    ReturnWrite,
    ReviewDecision,
    ReviewRuleView,
    ReviewRuleWrite,
    parse_order_id,
)
from app.services.order_desk import OrderDeskService
from app.services.order_query import OrderQueryService, parse_amount, parse_optional_id
from app.services.order_ship import OrderShipService

router = APIRouter(prefix="/orders", tags=["订单"])

OrderReader = Annotated[Identity, Depends(require_permission(Perm.ORDER_READ))]
OrderShipper = Annotated[Identity, Depends(require_permission(Perm.ORDER_SHIP))]
OrderWriter = Annotated[Identity, Depends(require_permission(Perm.ORDER_WRITE))]
OrderRule = Annotated[Identity, Depends(require_permission(Perm.ORDER_RULE))]


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
    queue: Annotated[str | None, Query(max_length=32)] = None,
    exception_kind: Annotated[str | None, Query(max_length=32)] = None,
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
        queue=queue,
        exception_kind=exception_kind,
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


@router.get("/freshness", response_model=ApiResponse[list[FreshnessView]], summary="订单数据新鲜度")
async def order_freshness(identity: OrderReader, session: DbSession) -> ApiResponse[list[FreshnessView]]:
    del identity
    rows = await OrderDeskService(session).freshness()
    return ok([FreshnessView.model_validate(row) for row in rows])


@router.get("/review-rules", response_model=ApiResponse[list[ReviewRuleView]], summary="审核规则")
async def list_review_rules(identity: OrderReader, session: DbSession) -> ApiResponse[list[ReviewRuleView]]:
    del identity
    rows = await OrderDeskService(session).list_rules()
    return ok(
        [ReviewRuleView(id=row.id, currency=row.currency, amount_gt=row.amount_gt, enabled=row.enabled) for row in rows]
    )


@router.put("/review-rules", response_model=ApiResponse[ReviewRuleView], summary="保存审核规则")
async def save_review_rule(
    payload: ReviewRuleWrite,
    identity: OrderRule,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ReviewRuleView]:
    row = await OrderDeskService(session).save_rule(
        tenant_id=identity.tenant.id,
        currency=payload.currency,
        amount_gt=payload.amount_gt,
        enabled=payload.enabled,
        user_id=identity.user.id,
    )
    return ok(ReviewRuleView(id=row.id, currency=row.currency, amount_gt=row.amount_gt, enabled=row.enabled))


@router.get("/returns", response_model=ApiResponse[list[ReturnView]], summary="退货退款单")
async def list_returns(
    identity: OrderReader,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ApiResponse[list[ReturnView]]:
    del identity
    rows = await OrderDeskService(session).list_returns(limit)
    return ok(
        [
            ReturnView(
                id=row.id,
                order_id=row.order_id,
                platform_order_id=platform_order_id,
                reason=row.reason,
                status=row.status,
                refund_amount=row.refund_amount,
                currency=row.currency,
                restock_flag=row.restock_flag,
                restock_sellable=row.restock_sellable,
                restock_status=row.restock_status,
                created_at=row.created_at,
            )
            for row, platform_order_id in rows
        ]
    )


@router.post("/returns/{return_id}/approve", response_model=ApiResponse[ReturnView], summary="审核通过退货单")
async def approve_return(
    return_id: str,
    identity: OrderWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ReturnView]:
    row = await OrderDeskService(session).transition_return(
        _order_id(return_id),
        action="approve",
        user_id=identity.user.id,
    )
    return ok(await _return_view(session, row))


@router.post("/returns/{return_id}/reject", response_model=ApiResponse[ReturnView], summary="拒绝退货单")
async def reject_return(
    return_id: str,
    identity: OrderWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ReturnView]:
    row = await OrderDeskService(session).transition_return(
        _order_id(return_id),
        action="reject",
        user_id=identity.user.id,
    )
    return ok(await _return_view(session, row))


@router.post("/returns/{return_id}/refund", response_model=ApiResponse[ReturnView], summary="完成退款")
async def refund_return(
    return_id: str,
    identity: OrderWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ReturnView]:
    row = await OrderDeskService(session).transition_return(
        _order_id(return_id),
        action="refund",
        user_id=identity.user.id,
    )
    return ok(await _return_view(session, row))


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


@router.post("/{order_id}/address", response_model=ApiResponse[OrderDetail], summary="修改收货地址")
async def change_address(
    order_id: str,
    payload: AddressWrite,
    identity: OrderWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[OrderDetail]:
    await OrderDeskService(session).change_address(
        _order_id(order_id),
        address=payload.model_dump(),
        user_id=identity.user.id,
    )
    data = await OrderQueryService(session).get_order(_order_id(order_id), role_code=identity.role_code)
    return ok(data)


@router.post("/{order_id}/notes", response_model=ApiResponse[NoteView], summary="添加备注")
async def add_note(
    order_id: str,
    payload: NoteWrite,
    identity: OrderWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[NoteView]:
    row = await OrderDeskService(session).add_note(
        _order_id(order_id),
        content=payload.content,
        user_id=identity.user.id,
    )
    return ok(NoteView(id=row.id, content=row.content, created_at=row.created_at, created_by=row.created_by))


@router.get("/{order_id}/notes", response_model=ApiResponse[list[NoteView]], summary="订单备注")
async def list_notes(order_id: str, identity: OrderReader, session: DbSession) -> ApiResponse[list[NoteView]]:
    del identity
    rows = await OrderDeskService(session).list_notes(_order_id(order_id))
    return ok(
        [NoteView(id=row.id, content=row.content, created_at=row.created_at, created_by=row.created_by) for row in rows]
    )


@router.get("/{order_id}/address-logs", response_model=ApiResponse[list[AddressLogView]], summary="改址历史")
async def list_address_logs(
    order_id: str,
    identity: OrderReader,
    session: DbSession,
) -> ApiResponse[list[AddressLogView]]:
    del identity
    rows = await OrderDeskService(session).list_address_logs(_order_id(order_id))
    return ok(
        [
            AddressLogView(
                id=row.id,
                status=row.status,
                failure_reason=row.failure_reason,
                before_address=None if row.before_address is None else AddressView.model_validate(row.before_address),
                after_address=AddressView.model_validate(row.after_address),
                created_at=row.created_at,
            )
            for row in rows
        ]
    )


@router.post("/{order_id}/review", response_model=ApiResponse[OrderDetail], summary="人工审核")
async def review_order(
    order_id: str,
    payload: ReviewDecision,
    identity: OrderWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[OrderDetail]:
    await OrderDeskService(session).decide_review(
        _order_id(order_id),
        decision=payload.decision,
        user_id=identity.user.id,
    )
    data = await OrderQueryService(session).get_order(_order_id(order_id), role_code=identity.role_code)
    return ok(data)


@router.post("/{order_id}/returns", response_model=ApiResponse[ReturnView], summary="创建退货单")
async def create_return(
    order_id: str,
    payload: ReturnWrite,
    identity: OrderWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ReturnView]:
    row = await OrderDeskService(session).create_return(
        _order_id(order_id),
        reason=payload.reason,
        refund_amount=payload.refund_amount,
        restock_flag=payload.restock_flag,
        restock_sellable=payload.restock_sellable,
        user_id=identity.user.id,
    )
    return ok(await _return_view(session, row))


async def _return_view(session: DbSession, row: ReturnOrder) -> ReturnView:
    platform_order_id = await OrderDeskService(session).platform_order_id(row.order_id)
    return ReturnView(
        id=row.id,
        order_id=row.order_id,
        platform_order_id=platform_order_id,
        reason=row.reason,
        status=row.status,
        refund_amount=row.refund_amount,
        currency=row.currency,
        restock_flag=row.restock_flag,
        restock_sellable=row.restock_sellable,
        restock_status=row.restock_status,
        created_at=row.created_at,
    )


def _order_id(value: str) -> int:
    try:
        return parse_order_id(value)
    except ValueError as exc:
        raise ParamInvalidError(str(exc)) from exc


__all__ = ["router"]
