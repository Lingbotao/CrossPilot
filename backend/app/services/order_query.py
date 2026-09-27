"""订单查询、脱敏与导出。搜索与列表共用同一套筛选。"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import SUPPORTED_PLATFORMS
from app.core.config import settings
from app.core.errors import ErrorCode, NotFoundError, ParamInvalidError, PlatformUnsupportedError
from app.core.pagination import PageData, PageInfo, decode_cursor, encode_cursor
from app.engines.order_privacy import mask_party
from app.engines.order_status import parse_unified
from app.models.enums import AuditAction
from app.models.order import OrderFee, OrderItem, OrderStatusLog
from app.repositories.identity import AuditLogRepository
from app.repositories.order import OrderStatusLogRepository
from app.repositories.order_read import OrderHit, OrderListQuery, OrderReadRepository
from app.schemas.common import money_to_str
from app.schemas.order import (
    AddressView,
    FilePayload,
    OrderDetail,
    OrderFeeView,
    OrderItemView,
    OrderListItem,
    PartyView,
    ShipmentView,
    TimelineView,
)
from app.schemas.order_status import StatusSourceCode
from app.services.order_xlsx import build_xlsx

EXPORT_FIELDS: tuple[str, ...] = (
    "platform_order_id",
    "platform_code",
    "shop_name",
    "site_code",
    "unified_status",
    "currency",
    "item_amount",
    "shipping_amount",
    "tax_amount",
    "discount_amount",
    "total_amount",
    "paid_at",
    "buyer_name",
    "fee_detail",
)
DEFAULT_EXPORT_FIELDS: tuple[str, ...] = (
    "platform_order_id",
    "unified_status",
    "currency",
    "total_amount",
    "fee_detail",
)
_EXPORT_LABELS: dict[str, str] = {
    "platform_order_id": "平台订单号",
    "platform_code": "平台",
    "shop_name": "店铺",
    "site_code": "站点",
    "unified_status": "状态",
    "currency": "币种",
    "item_amount": "商品金额",
    "shipping_amount": "运费",
    "tax_amount": "税费",
    "discount_amount": "折扣",
    "total_amount": "订单金额",
    "paid_at": "付款时间",
    "buyer_name": "买家",
    "fee_detail": "费用明细",
}


class OrderQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.reader = OrderReadRepository(session)
        self.logs = OrderStatusLogRepository(session)
        self.audit = AuditLogRepository(session)

    async def list_orders(
        self,
        *,
        role_code: str,
        limit: int,
        cursor: str | None,
        platform_code: str | None,
        shop_id: int | None,
        site_code: str | None,
        unified_status: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        amount_min: Decimal | None,
        amount_max: Decimal | None,
        sku: str | None,
        keyword: str | None,
    ) -> PageData[OrderListItem]:
        before_at, before_id = read_order_cursor(cursor)
        query = self._query(
            limit=limit + 1,
            platform_code=platform_code,
            shop_id=shop_id,
            site_code=site_code,
            unified_status=unified_status,
            created_from=created_from,
            created_to=created_to,
            amount_min=amount_min,
            amount_max=amount_max,
            sku=sku,
            keyword=keyword,
            before_created_at=before_at,
            before_id=before_id,
        )
        hits = await self.reader.list_orders(query)
        has_more = len(hits) > limit
        visible = hits[:limit]
        next_cursor = None
        if has_more and visible:
            last = visible[-1].order
            next_cursor = write_order_cursor(last.created_at, last.id)
        return PageData[OrderListItem](
            items=[_list_item(hit, role_code) for hit in visible],
            page_info=PageInfo(cursor=next_cursor, has_more=has_more),
        )

    async def get_order(self, order_id: int, *, role_code: str) -> OrderDetail:
        hit = await self.reader.get_order(order_id)
        if hit is None:
            raise NotFoundError("订单不存在", code=ErrorCode.ORDER_NOT_FOUND)
        items = await self.reader.list_items(hit.order.id)
        fees = await self.reader.list_fees([hit.order.id])
        logs = await self.logs.list_for_order(hit.order.id)
        return _detail(hit, role_code, items=items, fees=fees, logs=logs)

    async def export_orders(
        self,
        *,
        role_code: str,
        user_id: int,
        tenant_id: int,
        fields: list[str] | None,
        platform_code: str | None,
        shop_id: int | None,
        site_code: str | None,
        unified_status: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        amount_min: Decimal | None,
        amount_max: Decimal | None,
        sku: str | None,
        keyword: str | None,
    ) -> FilePayload:
        chosen = _export_fields(fields)
        cap = settings.order_export_max_rows
        query = self._query(
            limit=cap + 1,
            platform_code=platform_code,
            shop_id=shop_id,
            site_code=site_code,
            unified_status=unified_status,
            created_from=created_from,
            created_to=created_to,
            amount_min=amount_min,
            amount_max=amount_max,
            sku=sku,
            keyword=keyword,
            before_created_at=None,
            before_id=None,
        )
        hits = await self.reader.list_orders(query)
        truncated = len(hits) > cap
        visible = hits[:cap]
        fees = await self.reader.list_fees([hit.order.id for hit in visible])
        grouped: dict[int, list[OrderFee]] = {}
        for fee in fees:
            grouped.setdefault(fee.order_id, []).append(fee)
        headers = [_EXPORT_LABELS[name] for name in chosen]
        rows = [_export_row(hit, role_code, chosen, grouped.get(hit.order.id, [])) for hit in visible]
        payload = build_xlsx(headers, rows)
        await self.audit.append_action(
            tenant_id=tenant_id,
            action=AuditAction.DATA_EXPORT,
            resource="order",
            user_id=user_id,
            after={"fields": list(chosen), "row_count": len(rows), "truncated": truncated},
        )
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        return FilePayload(
            filename=f"orders-{stamp}.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content_base64=base64.b64encode(payload).decode("ascii"),
            row_count=len(rows),
            truncated=truncated,
        )

    def _query(
        self,
        *,
        limit: int,
        platform_code: str | None,
        shop_id: int | None,
        site_code: str | None,
        unified_status: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        amount_min: Decimal | None,
        amount_max: Decimal | None,
        sku: str | None,
        keyword: str | None,
        before_created_at: datetime | None,
        before_id: int | None,
    ) -> OrderListQuery:
        raw_platform = _clean_str(platform_code)
        platform = None if raw_platform is None else raw_platform.lower()
        if platform is not None and platform not in SUPPORTED_PLATFORMS:
            raise PlatformUnsupportedError()
        status = _clean_str(unified_status)
        if status is not None and parse_unified(status) is None:
            raise ParamInvalidError("统一状态不在九态之内")
        site = _clean_str(site_code)
        return OrderListQuery(
            limit=limit,
            platform_code=platform,
            shop_id=shop_id,
            site_code=None if site is None else site.upper(),
            unified_status=None if status is None else status.upper(),
            created_from=created_from,
            created_to=created_to,
            amount_min=amount_min,
            amount_max=amount_max,
            sku=_clean_str(sku),
            keyword=_clean_str(keyword),
            before_created_at=before_created_at,
            before_id=before_id,
        )


def parse_amount(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation as exc:
        raise ParamInvalidError("金额格式不正确") from exc


def parse_optional_id(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    if not text.isascii() or not text.isdigit():
        raise ParamInvalidError("店铺 ID 不正确")
    return int(text)


def read_order_cursor(cursor: str | None) -> tuple[datetime | None, int | None]:
    if not cursor:
        return None, None
    payload = decode_cursor(cursor)
    raw_time = payload.get("t")
    raw_id = payload.get("id")
    if not isinstance(raw_time, str) or not isinstance(raw_id, str) or not raw_id.isdigit():
        return None, None
    try:
        moment = datetime.fromisoformat(raw_time)
    except ValueError:
        return None, None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment, int(raw_id)


def write_order_cursor(created_at: datetime, order_id: int) -> str:
    moment = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)
    return encode_cursor({"t": moment.isoformat(), "id": str(order_id)})


def _export_fields(fields: list[str] | None) -> tuple[str, ...]:
    if not fields:
        return DEFAULT_EXPORT_FIELDS
    chosen: list[str] = []
    for name in fields:
        key = name.strip()
        if key not in EXPORT_FIELDS:
            raise ParamInvalidError(f"不支持导出字段 {key}")
        if key not in chosen:
            chosen.append(key)
    if not chosen:
        raise ParamInvalidError("请选择导出字段")
    return tuple(chosen)


def _clean_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _list_item(hit: OrderHit, role_code: str) -> OrderListItem:
    buyer, _ship = mask_party(role_code, hit.order.buyer_info, hit.order.ship_to)
    order = hit.order
    return OrderListItem(
        id=order.id,
        shop_id=order.shop_id,
        shop_name=hit.shop_name,
        site_code=hit.site_code,
        platform_code=order.platform_code,
        platform_order_id=order.platform_order_id,
        unified_status=order.unified_status,
        platform_status=order.platform_status,
        currency=order.currency,
        total_amount=order.total_amount,
        paid_at=order.paid_at,
        created_at=order.created_at,
        buyer_name=buyer["name"],
        shipment_status=hit.shipment_status,
        failure_reason=hit.failure_reason,
    )


def _detail(
    hit: OrderHit,
    role_code: str,
    *,
    items: list[OrderItem],
    fees: list[OrderFee],
    logs: list[OrderStatusLog],
) -> OrderDetail:
    buyer, ship = mask_party(role_code, hit.order.buyer_info, hit.order.ship_to)
    base = _list_item(hit, role_code)
    order = hit.order
    return OrderDetail(
        **base.model_dump(),
        item_amount=order.item_amount,
        shipping_amount=order.shipping_amount,
        tax_amount=order.tax_amount,
        discount_amount=order.discount_amount,
        shipped_at=order.shipped_at,
        buyer=PartyView.model_validate(buyer),
        ship_to=AddressView.model_validate(ship),
        items=[
            OrderItemView(
                id=item.id,
                platform_sku_id=item.platform_sku_id,
                platform_product_id=item.platform_product_id,
                item_name=item.item_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                currency=item.currency,
            )
            for item in items
        ],
        fees=[
            OrderFeeView(
                id=fee.id,
                fee_type=fee.fee_type,
                amount=fee.amount,
                currency=fee.currency,
                source=fee.source,
            )
            for fee in fees
        ],
        shipment=ShipmentView(
            carrier=hit.carrier,
            tracking_no=hit.tracking_no,
            status=hit.shipment_status,
            failure_reason=hit.failure_reason,
            attempt=hit.attempt,
            shipped_at=order.shipped_at,
        ),
        timeline=[
            TimelineView(
                id=row.id,
                from_status=row.from_status,
                to_status=row.to_status,
                platform_status=row.platform_status,
                operator_id=row.operator_id,
                source=_source(row.source),
                remark=row.remark,
                created_at=row.created_at,
            )
            for row in logs
        ],
        tracking_no=hit.tracking_no,
        carrier=hit.carrier,
    )


def _export_row(hit: OrderHit, role_code: str, fields: tuple[str, ...], fees: list[OrderFee]) -> list[str]:
    buyer, _ship = mask_party(role_code, hit.order.buyer_info, hit.order.ship_to)
    order = hit.order
    fee_detail = "; ".join(f"{fee.fee_type}:{money_to_str(fee.amount)} {fee.currency}" for fee in fees)
    values = {
        "platform_order_id": order.platform_order_id,
        "platform_code": order.platform_code,
        "shop_name": hit.shop_name,
        "site_code": hit.site_code,
        "unified_status": order.unified_status,
        "currency": order.currency,
        "item_amount": money_to_str(order.item_amount) or "",
        "shipping_amount": money_to_str(order.shipping_amount) or "",
        "tax_amount": money_to_str(order.tax_amount) or "",
        "discount_amount": money_to_str(order.discount_amount) or "",
        "total_amount": money_to_str(order.total_amount) or "",
        "paid_at": "" if order.paid_at is None else order.paid_at.isoformat(),
        "buyer_name": buyer["name"] or "",
        "fee_detail": fee_detail,
    }
    return [values[name] for name in fields]


def _source(value: str) -> StatusSourceCode:
    if value in {"SYSTEM", "WEBHOOK", "MANUAL"}:
        return value  # type: ignore[return-value]
    return "SYSTEM"


__all__ = [
    "DEFAULT_EXPORT_FIELDS",
    "EXPORT_FIELDS",
    "OrderQueryService",
    "parse_amount",
    "parse_optional_id",
    "read_order_cursor",
    "write_order_cursor",
]
