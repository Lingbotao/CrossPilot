"""订单读模型。列表走游标，筛选条件都带租户列。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Select, and_, case, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.order import SHIPMENT_FAILED, SHIPMENT_SUCCEEDED, OrderFee, OrderItem, SalesOrder, Shipment
from app.models.platform import Shop
from app.repositories.base import BaseRepository


@dataclass(frozen=True, slots=True)
class OrderListQuery:
    limit: int
    platform_code: str | None = None
    shop_id: int | None = None
    site_code: str | None = None
    unified_status: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    amount_min: Decimal | None = None
    amount_max: Decimal | None = None
    sku: str | None = None
    keyword: str | None = None
    before_created_at: datetime | None = None
    before_id: int | None = None
    queue: str | None = None
    exception_kind: str | None = None
    sla_hours: int = 48
    warn_hours: int = 4
    as_of: datetime | None = None
    before_rank: int | None = None
    before_paid_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OrderHit:
    order: SalesOrder
    shop_name: str
    site_code: str
    shipment_status: str | None
    failure_reason: str | None
    tracking_no: str | None
    carrier: str | None
    attempt: int | None


class OrderReadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_orders(self, query: OrderListQuery) -> list[OrderHit]:
        if query.queue == "to_ship":
            rank = _ship_rank(query)
            stmt = self._select().order_by(rank.asc(), SalesOrder.paid_at.asc().nulls_last(), SalesOrder.id.desc())
        else:
            stmt = self._select().order_by(SalesOrder.created_at.desc(), SalesOrder.id.desc())
        stmt = self._filter(stmt, query).limit(query.limit)
        rows = (await self.session.execute(stmt)).all()
        return [_hit(row) for row in rows]  # row is a SQLAlchemy Row

    async def get_order(self, order_id: int) -> OrderHit | None:
        stmt = self._select().where(SalesOrder.id == order_id).limit(1)
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return None
        return _hit(row)

    async def unmatched_order_ids(self, order_ids: list[int]) -> set[int]:
        if not order_ids:
            return set()
        stmt = (
            select(OrderItem.order_id).where(OrderItem.order_id.in_(order_ids), OrderItem.sku_id.is_(None)).distinct()
        )
        return {int(row) for row in (await self.session.execute(stmt)).scalars().all()}

    async def list_items(self, order_id: int) -> list[OrderItem]:
        stmt = select(OrderItem).where(OrderItem.order_id == order_id).order_by(OrderItem.id.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_fees(self, order_ids: list[int]) -> list[OrderFee]:
        if not order_ids:
            return []
        stmt = select(OrderFee).where(OrderFee.order_id.in_(order_ids)).order_by(OrderFee.id.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    def _select(self) -> Select[Any]:
        return (
            select(
                SalesOrder,
                Shop.shop_name,
                Shop.site_code,
                Shipment.status,
                Shipment.failure_reason,
                Shipment.tracking_no,
                Shipment.carrier,
                Shipment.attempt,
            )
            .outerjoin(Shop, Shop.id == SalesOrder.shop_id)
            .outerjoin(Shipment, Shipment.order_id == SalesOrder.id)
        )

    def _filter(self, stmt: Select[Any], query: OrderListQuery) -> Select[Any]:
        if query.platform_code:
            stmt = stmt.where(SalesOrder.platform_code == query.platform_code)
        if query.shop_id is not None:
            stmt = stmt.where(SalesOrder.shop_id == query.shop_id)
        if query.site_code:
            stmt = stmt.where(Shop.site_code == query.site_code)
        if query.unified_status:
            stmt = stmt.where(SalesOrder.unified_status == query.unified_status)
        if query.created_from is not None:
            stmt = stmt.where(SalesOrder.created_at >= query.created_from)
        if query.created_to is not None:
            stmt = stmt.where(SalesOrder.created_at <= query.created_to)
        if query.amount_min is not None:
            stmt = stmt.where(SalesOrder.total_amount >= query.amount_min)
        if query.amount_max is not None:
            stmt = stmt.where(SalesOrder.total_amount <= query.amount_max)
        if query.sku:
            stmt = stmt.where(_sku_exists(_contains(query.sku)))
        if query.keyword:
            stmt = stmt.where(_keyword(query.keyword))
        if query.queue == "to_ship":
            stmt = stmt.where(SalesOrder.unified_status == "PAID")
            stmt = stmt.where(SalesOrder.review_status.in_(("AUTO_PASSED", "APPROVED")))
            stmt = _deadline_page(stmt, query)
        elif query.queue == "exception" or query.exception_kind:
            stmt = stmt.where(_exception_clause(query))
        if query.queue != "to_ship" and query.before_created_at is not None and query.before_id is not None:
            stmt = stmt.where(
                or_(
                    SalesOrder.created_at < query.before_created_at,
                    and_(SalesOrder.created_at == query.before_created_at, SalesOrder.id < query.before_id),
                )
            )
        return stmt


def _clock(query: OrderListQuery) -> datetime:
    return query.as_of or datetime.now(UTC)


def _ship_rank(query: OrderListQuery) -> Any:
    now = _clock(query)
    overdue_before = now - timedelta(hours=query.sla_hours)
    soon_before = now - timedelta(hours=max(query.sla_hours - query.warn_hours, 0))
    return case(
        (and_(SalesOrder.paid_at.is_not(None), SalesOrder.paid_at <= overdue_before), 0),
        (and_(SalesOrder.paid_at.is_not(None), SalesOrder.paid_at <= soon_before), 1),
        else_=2,
    )


def _deadline_page(stmt: Select[Any], query: OrderListQuery) -> Select[Any]:
    if query.before_rank is None or query.before_id is None:
        return stmt
    rank = _ship_rank(query)
    paid = query.before_paid_at
    same_rank = rank == query.before_rank
    later = [rank > query.before_rank]
    if paid is None:
        later.append(and_(same_rank, SalesOrder.paid_at.is_(None), SalesOrder.id < query.before_id))
    else:
        later.append(and_(same_rank, SalesOrder.paid_at > paid))
        later.append(and_(same_rank, SalesOrder.paid_at == paid, SalesOrder.id < query.before_id))
        later.append(and_(same_rank, SalesOrder.paid_at.is_(None)))
    return stmt.where(or_(*later))


def _exception_clause(query: OrderListQuery) -> ColumnElement[bool]:
    kind = query.exception_kind
    now = _clock(query)
    overdue_before = now - timedelta(hours=query.sla_hours)
    soon_before = now - timedelta(hours=max(query.sla_hours - query.warn_hours, 0))
    country = SalesOrder.ship_to["country"].astext
    line1 = SalesOrder.ship_to["line1"].astext
    city = SalesOrder.ship_to["city"].astext
    parts: list[ColumnElement[bool]] = []
    if kind in (None, "SKU_UNMATCHED"):
        parts.append(
            exists(select(OrderItem.id).where(OrderItem.order_id == SalesOrder.id, OrderItem.sku_id.is_(None)))
        )
    if kind in (None, "ADDRESS_INVALID"):
        parts.append(
            or_(
                SalesOrder.ship_to.is_(None),
                country.is_(None),
                country == "",
                and_(or_(line1.is_(None), line1 == ""), or_(city.is_(None), city == "")),
            )
        )
    paid_open = and_(SalesOrder.unified_status == "PAID", SalesOrder.paid_at.is_not(None))
    if kind == "SHIP_OVERDUE":
        parts.append(and_(paid_open, SalesOrder.paid_at <= overdue_before))
    elif kind == "SHIP_DUE_SOON":
        parts.append(and_(paid_open, SalesOrder.paid_at <= soon_before, SalesOrder.paid_at > overdue_before))
    elif kind is None:
        parts.append(and_(paid_open, SalesOrder.paid_at <= soon_before))
    if kind in (None, "REVIEW_PENDING"):
        parts.append(SalesOrder.review_status == "PENDING")
    return or_(*parts)


class ShipmentRepository(BaseRepository[Shipment]):
    model = Shipment

    async def get_for_order(self, order_id: int) -> Shipment | None:
        stmt = self.base_select().where(Shipment.order_id == order_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def save(
        self,
        *,
        tenant_id: int,
        order_id: int,
        carrier: str,
        tracking_no: str,
        status: str,
        failure_reason: str | None,
        shipped_at: datetime | None,
        attempt: int,
        operator_id: int | None,
    ) -> Shipment:
        if status not in {SHIPMENT_SUCCEEDED, SHIPMENT_FAILED}:
            raise ValueError("发货状态不在允许范围内")
        row = await self.get_for_order(order_id)
        if row is None:
            row = Shipment(
                tenant_id=tenant_id,
                order_id=order_id,
                carrier=carrier[:64],
                tracking_no=tracking_no[:64],
                status=status,
                failure_reason=None if failure_reason is None else failure_reason[:500],
                shipped_at=shipped_at,
                attempt=attempt,
                created_by=operator_id,
                updated_by=operator_id,
            )
            self.session.add(row)
        else:
            row.carrier = carrier[:64]
            row.tracking_no = tracking_no[:64]
            row.status = status
            row.failure_reason = None if failure_reason is None else failure_reason[:500]
            row.shipped_at = shipped_at
            row.attempt = attempt
            row.updated_by = operator_id
        await self.session.flush()
        return row


def _hit(row: Any) -> OrderHit:
    order = cast(SalesOrder, row[0])
    shop_name = cast(str | None, row[1])
    site_code = cast(str | None, row[2])
    status = cast(str | None, row[3])
    reason = cast(str | None, row[4])
    tracking = cast(str | None, row[5])
    carrier = cast(str | None, row[6])
    attempt = cast(int | None, row[7])
    return OrderHit(
        order=order,
        shop_name=shop_name or "",
        site_code=site_code or "",
        shipment_status=status,
        failure_reason=reason,
        tracking_no=tracking,
        carrier=carrier,
        attempt=attempt,
    )


def _contains(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _sku_exists(pattern: str) -> ColumnElement[bool]:
    return exists(
        select(OrderItem.id).where(
            OrderItem.order_id == SalesOrder.id,
            OrderItem.tenant_id == SalesOrder.tenant_id,
            or_(
                OrderItem.platform_sku_id.ilike(pattern, escape="\\"),
                OrderItem.item_name.ilike(pattern, escape="\\"),
            ),
        )
    )


def _keyword(keyword: str) -> ColumnElement[bool]:
    pattern = _contains(keyword)
    clauses = [
        SalesOrder.platform_order_id.ilike(pattern, escape="\\"),
        SalesOrder.buyer_info["name"].astext.ilike(pattern, escape="\\"),
        _sku_exists(pattern),
    ]
    if keyword.isdigit() and len(keyword) == 4:
        clauses.append(SalesOrder.buyer_info["phone_last4"].astext == keyword)
    return or_(*clauses)


__all__ = ["OrderHit", "OrderListQuery", "OrderReadRepository", "ShipmentRepository"]
