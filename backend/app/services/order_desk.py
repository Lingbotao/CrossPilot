"""审核、改址、备注、退货。改址和备注必须等平台确认后才落库。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError
from app.adapters.registry import adapter_registry
from app.core.config import settings
from app.core.errors import AppError, ErrorCode, NotFoundError, ParamInvalidError
from app.engines.order_desk import (
    AmountRule,
    ReturnStatus,
    address_invalid,
    decide_return,
    next_review_status,
    restock_after_refund,
)
from app.engines.order_status import DecisionKind, StatusChangeSource, UnifiedStatus, decide_status, parse_unified
from app.models.order import OrderAddressLog, OrderNote, OrderReviewRule, ReturnOrder, SalesOrder
from app.repositories.order import OrderStatusLogRepository, SalesOrderRepository
from app.repositories.order_desk import (
    OrderAddressLogRepository,
    OrderNoteRepository,
    OrderReviewRuleRepository,
    ReturnOrderRepository,
)
from app.repositories.platform import ShopCredentialRepository, ShopRepository
from app.services.credential_service import view_from_row
from app.services.shop_health import freshness_line

_ADDRESS_KEYS = ("name", "phone", "country", "state", "city", "line1", "postal_code")


class OrderDeskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = SalesOrderRepository(session)
        self.rules = OrderReviewRuleRepository(session)
        self.notes = OrderNoteRepository(session)
        self.addresses = OrderAddressLogRepository(session)
        self.returns = ReturnOrderRepository(session)
        self.logs = OrderStatusLogRepository(session)
        self.shops = ShopRepository(session)
        self.credentials = ShopCredentialRepository(session)
        register_builtin_adapters()

    async def list_rules(self) -> list[OrderReviewRule]:
        return await self.rules.list_rules()

    async def save_rule(
        self,
        *,
        tenant_id: int,
        currency: str,
        amount_gt: Decimal,
        enabled: bool,
        user_id: int,
    ) -> OrderReviewRule:
        code = currency.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ParamInvalidError("币种需要 3 位字母")
        if amount_gt < 0:
            raise ParamInvalidError("审核金额不能为负")
        return await self.rules.save_rule(
            tenant_id=tenant_id,
            currency=code,
            amount_gt=amount_gt,
            enabled=enabled,
            user_id=user_id,
        )

    async def apply_review(self, order: SalesOrder) -> None:
        stored = await self.rules.list_rules()
        rules = [AmountRule(currency=row.currency, amount_gt=row.amount_gt, enabled=row.enabled) for row in stored]
        order.review_status = next_review_status(
            order.review_status,
            total=order.total_amount,
            currency=order.currency,
            rules=rules,
        )

    async def decide_review(self, order_id: int, *, decision: str, user_id: int) -> SalesOrder:
        order = await self._order(order_id)
        if order.review_status != "PENDING":
            raise AppError("只有待审核订单可以人工裁定", code=ErrorCode.ORDER_STATE_INVALID)
        if decision == "approve":
            order.review_status = "APPROVED"
        elif decision == "reject":
            order.review_status = "REJECTED"
        else:
            raise ParamInvalidError("审核决定只能是通过或拒绝")
        order.updated_by = user_id
        await self.session.flush()
        return order

    async def change_address(
        self,
        order_id: int,
        *,
        address: dict[str, str],
        user_id: int,
    ) -> SalesOrder:
        order = await self._order(order_id)
        after = _address(address)
        if address_invalid(after):
            raise ParamInvalidError("收货地址需要国家，以及城市或详细地址")
        before = dict(order.ship_to) if order.ship_to else None
        try:
            await self._push(order, kind="address", address=after, content=None)
        except AdapterError as exc:
            await self.addresses.insert(
                tenant_id=order.tenant_id,
                order_id=order.id,
                before_address=before,
                after_address=after,
                status="FAILED",
                failure_reason=str(exc),
                user_id=user_id,
            )
            raise AppError(str(exc) or "平台未接受新地址", code=ErrorCode.ORDER_PLATFORM_REJECTED) from exc
        order.ship_to = after
        order.updated_by = user_id
        await self.addresses.insert(
            tenant_id=order.tenant_id,
            order_id=order.id,
            before_address=before,
            after_address=after,
            status="SUCCEEDED",
            failure_reason=None,
            user_id=user_id,
        )
        await self.session.flush()
        return order

    async def add_note(self, order_id: int, *, content: str, user_id: int) -> OrderNote:
        order = await self._order(order_id)
        text = content.strip()
        if not text:
            raise ParamInvalidError("备注不能为空")
        if len(text) > 500:
            raise ParamInvalidError("备注不能超过 500 字")
        try:
            await self._push(order, kind="note", address=None, content=text)
        except AdapterError as exc:
            raise AppError(str(exc) or "平台未接受备注", code=ErrorCode.ORDER_PLATFORM_REJECTED) from exc
        return await self.notes.insert(tenant_id=order.tenant_id, order_id=order.id, content=text, user_id=user_id)

    async def platform_order_id(self, order_id: int) -> str:
        order = await self.orders.get(order_id)
        return "" if order is None else order.platform_order_id

    async def list_notes(self, order_id: int) -> list[OrderNote]:
        await self._order(order_id)
        return await self.notes.list_for_order(order_id)

    async def list_address_logs(self, order_id: int) -> list[OrderAddressLog]:
        await self._order(order_id)
        return await self.addresses.list_for_order(order_id)

    async def list_returns(self, limit: int) -> list[tuple[ReturnOrder, str]]:
        rows = await self.returns.list_recent(limit)
        paired: list[tuple[ReturnOrder, str]] = []
        for row in rows:
            order = await self.orders.get(row.order_id)
            paired.append((row, "" if order is None else order.platform_order_id))
        return paired

    async def create_return(
        self,
        order_id: int,
        *,
        reason: str,
        refund_amount: Decimal,
        restock_flag: bool,
        restock_sellable: bool,
        user_id: int,
    ) -> ReturnOrder:
        order = await self._order(order_id)
        text = reason.strip()
        if not text:
            raise ParamInvalidError("请填写退货原因")
        if refund_amount < 0 or refund_amount > order.total_amount:
            raise ParamInvalidError("退款金额需要在 0 和订单金额之间")
        row = ReturnOrder(
            tenant_id=order.tenant_id,
            order_id=order.id,
            reason=text[:500],
            status=ReturnStatus.REQUESTED.value,
            refund_amount=refund_amount,
            currency=order.currency,
            restock_flag=restock_flag,
            restock_sellable=restock_sellable,
            restock_status="NONE",
            created_by=user_id,
            updated_by=user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def transition_return(self, return_id: int, *, action: str, user_id: int) -> ReturnOrder:
        row = await self.returns.get(return_id)
        if row is None:
            raise NotFoundError("退货单不存在", code=ErrorCode.ORDER_NOT_FOUND)
        nxt = decide_return(row.status, action)
        if nxt is None:
            raise AppError("退货单当前状态不能做这个操作", code=ErrorCode.RETURN_STATE_INVALID)
        order = await self._order(row.order_id)
        if action == "approve":
            await self._move_order(order, UnifiedStatus.REFUNDING, user_id, remark="退货审核通过")
        elif action == "refund":
            await self._move_order(order, UnifiedStatus.REFUNDED, user_id, remark="退款完成")
            row.restock_status = restock_after_refund(restock_flag=row.restock_flag)
            if row.restock_flag:
                from app.services.inventory import InventoryService

                await InventoryService(self.session).receive_return(row, actor_id=user_id)
        row.status = nxt
        row.updated_by = user_id
        await self.session.flush()
        return row

    async def freshness(self) -> list[dict[str, Any]]:
        shops = await self.shops.list_open()
        now = datetime.now(UTC)
        stale = timedelta(minutes=settings.shop_sync_stale_minutes)
        lines: list[dict[str, Any]] = []
        for shop in shops:
            message = freshness_line(
                last_sync_status=shop.last_sync_status,
                last_sync_at=shop.last_sync_at,
                now=now,
                stale=stale,
            )
            if message is None:
                continue
            lines.append(
                {
                    "shop_id": shop.id,
                    "shop_name": shop.shop_name,
                    "platform_code": shop.platform_code,
                    "last_sync_at": shop.last_sync_at,
                    "message": message,
                }
            )
        return lines

    async def _order(self, order_id: int) -> SalesOrder:
        order = await self.orders.get(order_id)
        if order is None:
            raise NotFoundError("订单不存在", code=ErrorCode.ORDER_NOT_FOUND)
        return order

    async def _push(
        self,
        order: SalesOrder,
        *,
        kind: str,
        address: dict[str, str] | None,
        content: str | None,
    ) -> None:
        shop = await self.shops.get(order.shop_id)
        if shop is None:
            raise NotFoundError("订单不存在", code=ErrorCode.ORDER_NOT_FOUND)
        cred = await self.credentials.get_by_shop_id(shop.id)
        if cred is None:
            raise AppError("店铺凭证不存在", code=ErrorCode.ORDER_PLATFORM_REJECTED)
        view = view_from_row(shop, cred)
        adapter = adapter_registry.get(shop.platform_code)
        if kind == "address":
            await adapter.update_address(view, order.platform_order_id, address or {})
            return
        await adapter.update_note(view, order.platform_order_id, content or "")

    async def _move_order(self, order: SalesOrder, target: UnifiedStatus, user_id: int, *, remark: str) -> None:
        decision = decide_status(parse_unified(order.unified_status), target, source=StatusChangeSource.MANUAL)
        if decision.kind is not DecisionKind.APPLY:
            raise AppError("当前订单状态不能进入退款流程", code=ErrorCode.ORDER_STATE_INVALID)
        previous = order.unified_status
        order.unified_status = target.value
        order.updated_by = user_id
        await self.logs.append(
            tenant_id=order.tenant_id,
            order_id=order.id,
            from_status=previous,
            to_status=target.value,
            platform_status=order.platform_status,
            operator_id=user_id,
            source=StatusChangeSource.MANUAL.value,
            remark=remark,
        )


def _address(raw: dict[str, str]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key in _ADDRESS_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            cleaned[key] = value.strip()
    if "country" in cleaned:
        cleaned["country"] = cleaned["country"].upper()
    return cleaned


__all__ = ["OrderDeskService"]
