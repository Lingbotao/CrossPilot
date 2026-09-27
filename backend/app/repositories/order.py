"""订单写入。业务层不碰 Session，幂等判定在入库前完成。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app.adapters.base import UnifiedOrder
from app.engines.order_status import DecisionKind, StatusDecision
from app.models.order import ORDER_MODULE, OrderItem, OrderStatusLog, SalesOrder, ShopSyncCursor
from app.models.platform import Shop
from app.repositories.base import BaseRepository
from app.sync_engine.cursor import CursorCheckpoint
from app.sync_engine.idempotency import WriteAction, decide_write, order_idempotency_key

_PLACES = Decimal("0.000001")
_PAID_OR_LATER = frozenset({"PAID", "SHIPPED", "DELIVERED", "COMPLETED"})
_SHIPPED_OR_LATER = frozenset({"SHIPPED", "DELIVERED", "COMPLETED"})
_DELIVERED_OR_LATER = frozenset({"DELIVERED", "COMPLETED"})
StatusResolver = Callable[[str | None], StatusDecision]


def _money(value: Decimal) -> Decimal:
    return value.quantize(_PLACES)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SalesOrderRepository(BaseRepository[SalesOrder]):
    model = SalesOrder

    async def get_by_key(self, key: str) -> SalesOrder | None:
        stmt = self.base_select().where(SalesOrder.idempotency_key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self,
        shop: Shop,
        order: UnifiedOrder,
        *,
        resolve: StatusResolver,
    ) -> tuple[WriteAction, StatusDecision | None]:
        key = order_idempotency_key(shop.platform_code, shop.id, order.platform_order_id)
        existing = await self.get_by_key(key)
        action = decide_write(None if existing is None else existing.platform_updated_at, order.updated_at)
        if action is WriteAction.SKIP:
            return action, None
        try:
            async with self.session.begin_nested():
                if existing is None:
                    decision = resolve(None)
                    row = _new_order(shop, order, key, decision)
                    self.session.add(row)
                    await self.session.flush()
                    await self._replace_items(row, order)
                    return WriteAction.INSERT, decision
                decision = resolve(existing.unified_status)
                _apply_status(existing, order, decision)
                await self._replace_items(existing, order)
                await self.session.flush()
                return WriteAction.UPDATE, decision
        except IntegrityError:
            raced = await self.get_by_key(key)
            if raced is None:
                raise
            if decide_write(raced.platform_updated_at, order.updated_at) is not WriteAction.UPDATE:
                return WriteAction.SKIP, None
            decision = resolve(raced.unified_status)
            _apply_status(raced, order, decision)
            await self._replace_items(raced, order)
            await self.session.flush()
            return WriteAction.UPDATE, decision

    async def _replace_items(self, row: SalesOrder, order: UnifiedOrder) -> None:
        await self.session.execute(
            delete(OrderItem).where(OrderItem.tenant_id == row.tenant_id, OrderItem.order_id == row.id)
        )
        now = datetime.now(UTC)
        for item in order.items:
            self.session.add(
                OrderItem(
                    tenant_id=row.tenant_id,
                    created_at=now,
                    order_id=row.id,
                    platform_sku_id=item.platform_sku_id[:128],
                    platform_product_id=item.platform_product_id[:128],
                    item_name=item.item_name[:512],
                    quantity=item.quantity,
                    unit_price=_money(item.unit_price),
                    currency=row.currency,
                )
            )
        await self.session.flush()


class ShopSyncCursorRepository(BaseRepository[ShopSyncCursor]):
    model = ShopSyncCursor

    async def get_order_cursor(self, shop_id: int) -> ShopSyncCursor | None:
        stmt = self.base_select().where(ShopSyncCursor.shop_id == shop_id, ShopSyncCursor.module == ORDER_MODULE)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def save(self, shop: Shop, checkpoint: CursorCheckpoint) -> ShopSyncCursor:
        row = await self.get_order_cursor(shop.id)
        if row is None:
            row = ShopSyncCursor(
                tenant_id=shop.tenant_id,
                shop_id=shop.id,
                module=ORDER_MODULE,
                cursor_at=checkpoint.cursor_at,
                resume_cursor=checkpoint.resume_cursor,
                resume_since=checkpoint.resume_since,
                resume_until=checkpoint.resume_until,
            )
            self.session.add(row)
        else:
            if checkpoint.cursor_at is not None and (row.cursor_at is None or checkpoint.cursor_at > row.cursor_at):
                row.cursor_at = checkpoint.cursor_at
            row.resume_cursor = checkpoint.resume_cursor
            row.resume_since = checkpoint.resume_since
            row.resume_until = checkpoint.resume_until
        await self.session.flush()
        return row


class OrderStatusLogRepository(BaseRepository[OrderStatusLog]):
    model = OrderStatusLog

    async def append(
        self,
        *,
        tenant_id: int,
        order_id: int,
        from_status: str | None,
        to_status: str,
        platform_status: str,
        operator_id: int | None,
        source: str,
        remark: str | None,
    ) -> OrderStatusLog:
        row = OrderStatusLog(
            tenant_id=tenant_id,
            created_at=datetime.now(UTC),
            order_id=order_id,
            from_status=from_status,
            to_status=to_status,
            platform_status=(platform_status or "")[:64],
            operator_id=operator_id,
            source=source,
            remark=remark,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_order(self, order_id: int) -> list[OrderStatusLog]:
        stmt = (
            self.base_select()
            .where(OrderStatusLog.order_id == order_id)
            .order_by(OrderStatusLog.created_at.asc(), OrderStatusLog.id.asc())
            .limit(200)
        )
        return list((await self.session.execute(stmt)).scalars().all())


def _line_amount(order: UnifiedOrder) -> Decimal:
    if not order.items:
        return _money(order.total_amount)
    total = Decimal("0")
    for item in order.items:
        total += item.unit_price * item.quantity
    return _money(total)


def _buyer(order: UnifiedOrder) -> dict[str, str] | None:
    payload: dict[str, str] = {}
    if order.buyer_name:
        payload["name"] = order.buyer_name
    if order.buyer_country:
        payload["country"] = order.buyer_country
    return payload or None


def _ship_to(order: UnifiedOrder) -> dict[str, str] | None:
    if not order.buyer_country:
        return None
    return {"country": order.buyer_country}


def _new_order(shop: Shop, order: UnifiedOrder, key: str, decision: StatusDecision) -> SalesOrder:
    row = SalesOrder(
        tenant_id=shop.tenant_id,
        shop_id=shop.id,
        platform_code=shop.platform_code,
        platform_order_id=order.platform_order_id,
        idempotency_key=key,
        platform_status="",
        unified_status=decision.to_status.value,
        currency=order.currency,
        item_amount=Decimal("0.000000"),
        shipping_amount=Decimal("0.000000"),
        tax_amount=Decimal("0.000000"),
        discount_amount=Decimal("0.000000"),
        total_amount=Decimal("0.000000"),
    )
    _apply_status(row, order, decision)
    return row


def _apply_status(row: SalesOrder, order: UnifiedOrder, decision: StatusDecision) -> None:
    row.platform_status = (order.platform_status or decision.to_status.value)[:64]
    row.unified_status = decision.to_status.value
    row.buyer_info = _buyer(order)
    row.ship_to = _ship_to(order)
    row.currency = order.currency
    row.item_amount = _line_amount(order)
    row.total_amount = _money(order.total_amount)
    row.platform_updated_at = _as_utc(order.updated_at)
    if order.paid_at is not None:
        row.paid_at = _as_utc(order.paid_at)
    row.raw_payload = order.raw
    if decision.kind == DecisionKind.APPLY:
        _stamp(row, decision.to_status.value, order)


def _stamp(row: SalesOrder, status: str, order: UnifiedOrder) -> None:
    moment = _as_utc(order.updated_at)
    if status in _PAID_OR_LATER and row.paid_at is None:
        row.paid_at = _as_utc(order.paid_at) if order.paid_at else moment
    if status in _SHIPPED_OR_LATER and row.shipped_at is None:
        row.shipped_at = moment
    if status in _DELIVERED_OR_LATER and row.delivered_at is None:
        row.delivered_at = moment


__all__ = ["OrderStatusLogRepository", "SalesOrderRepository", "ShopSyncCursorRepository"]
