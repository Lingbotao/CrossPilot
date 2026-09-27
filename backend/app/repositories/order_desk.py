"""审核规则、备注、改址历史、退货单。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import OrderAddressLog, OrderNote, OrderReviewRule, ReturnOrder
from app.repositories.base import BaseRepository


class OrderReviewRuleRepository(BaseRepository[OrderReviewRule]):
    model = OrderReviewRule

    async def list_rules(self) -> list[OrderReviewRule]:
        stmt = self.base_select().order_by(OrderReviewRule.currency.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def save_rule(
        self,
        *,
        tenant_id: int,
        currency: str,
        amount_gt: Decimal,
        enabled: bool,
        user_id: int,
    ) -> OrderReviewRule:
        row = await self.get_by(currency=currency)
        if row is None:
            row = OrderReviewRule(
                tenant_id=tenant_id,
                currency=currency,
                amount_gt=amount_gt,
                enabled=enabled,
                created_by=user_id,
                updated_by=user_id,
            )
            self.session.add(row)
        else:
            row.amount_gt = amount_gt
            row.enabled = enabled
            row.updated_by = user_id
        await self.session.flush()
        return row


class OrderNoteRepository(BaseRepository[OrderNote]):
    model = OrderNote

    async def insert(self, *, tenant_id: int, order_id: int, content: str, user_id: int) -> OrderNote:
        row = OrderNote(tenant_id=tenant_id, order_id=order_id, content=content, created_by=user_id)
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_order(self, order_id: int) -> list[OrderNote]:
        stmt = self.base_select().where(OrderNote.order_id == order_id).order_by(OrderNote.created_at.asc())
        return list((await self.session.execute(stmt)).scalars().all())


class OrderAddressLogRepository(BaseRepository[OrderAddressLog]):
    model = OrderAddressLog

    async def insert(
        self,
        *,
        tenant_id: int,
        order_id: int,
        before_address: dict[str, Any] | None,
        after_address: dict[str, Any],
        status: str,
        failure_reason: str | None,
        user_id: int,
    ) -> OrderAddressLog:
        row = OrderAddressLog(
            tenant_id=tenant_id,
            order_id=order_id,
            before_address=before_address,
            after_address=after_address,
            status=status,
            failure_reason=None if failure_reason is None else failure_reason[:500],
            created_by=user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_order(self, order_id: int) -> list[OrderAddressLog]:
        stmt = self.base_select().where(OrderAddressLog.order_id == order_id).order_by(OrderAddressLog.created_at.asc())
        return list((await self.session.execute(stmt)).scalars().all())


class ReturnOrderRepository(BaseRepository[ReturnOrder]):
    model = ReturnOrder

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_recent(self, limit: int) -> list[ReturnOrder]:
        stmt = self.base_select().order_by(ReturnOrder.created_at.desc(), ReturnOrder.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())
