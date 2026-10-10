"""客服消息、模板和工单的读写。口径在 engines/cs.py。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engines.cs import SLA_OK, SLA_OVERDUE, SLA_WARN
from app.models.cs import CsMessage, CsTemplate, CsTicket, CsTicketNote
from app.models.enums import ShopStatus, TenantUserStatus
from app.models.order import ReturnOrder, SalesOrder, Shipment
from app.models.platform import Shop
from app.models.tenant import SysUser, TenantUser


class CsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def active_shops(self) -> list[Shop]:
        stmt = (
            select(Shop)
            .where(Shop.deleted_at.is_(None), Shop.status == int(ShopStatus.ACTIVE))
            .order_by(Shop.id.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def find_message(self, shop_id: int, platform_message_id: str) -> CsMessage | None:
        stmt = select(CsMessage).where(
            CsMessage.shop_id == shop_id,
            CsMessage.platform_message_id == platform_message_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_message(self, message_id: int) -> CsMessage | None:
        stmt = select(CsMessage).where(CsMessage.id == message_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_messages(
        self,
        *,
        shop_id: int | None,
        status: str | None,
        sla: str | None,
        now: datetime,
        warn: timedelta,
        before_id: int | None,
        limit: int,
    ) -> list[CsMessage]:
        stmt = select(CsMessage)
        if shop_id is not None:
            stmt = stmt.where(CsMessage.shop_id == shop_id)
        if status is not None:
            stmt = stmt.where(CsMessage.status == status)
        if sla == SLA_OVERDUE:
            stmt = stmt.where(CsMessage.sla_deadline <= now)
        elif sla == SLA_WARN:
            stmt = stmt.where(CsMessage.sla_deadline > now, CsMessage.sla_deadline <= now + warn)
        elif sla == SLA_OK:
            stmt = stmt.where(CsMessage.sla_deadline > now + warn)
        if before_id is not None:
            stmt = stmt.where(CsMessage.id < before_id)
        stmt = stmt.order_by(CsMessage.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def add_message(self, row: CsMessage) -> CsMessage:
        self.session.add(row)
        await self.session.flush()
        return row

    async def find_order(self, shop_id: int, platform_order_id: str) -> SalesOrder | None:
        stmt = select(SalesOrder).where(
            SalesOrder.shop_id == shop_id,
            SalesOrder.platform_order_id == platform_order_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_order(self, order_id: int) -> SalesOrder | None:
        stmt = select(SalesOrder).where(SalesOrder.id == order_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def tracking_no(self, order_id: int) -> str | None:
        stmt = select(Shipment.tracking_no).where(Shipment.order_id == order_id)
        value = (await self.session.execute(stmt)).scalar_one_or_none()
        if not isinstance(value, str) or not value.strip():
            return None
        return value.strip()

    async def get_return(self, return_id: int) -> ReturnOrder | None:
        stmt = select(ReturnOrder).where(ReturnOrder.id == return_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_templates(self, limit: int) -> list[CsTemplate]:
        stmt = (
            select(CsTemplate)
            .where(CsTemplate.deleted_at.is_(None))
            .order_by(CsTemplate.scene, CsTemplate.lang, CsTemplate.name)
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_template(self, template_id: int) -> CsTemplate | None:
        stmt = select(CsTemplate).where(CsTemplate.id == template_id, CsTemplate.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_template(self, scene: str, lang: str, name: str) -> CsTemplate | None:
        stmt = select(CsTemplate).where(
            CsTemplate.scene == scene,
            CsTemplate.lang == lang,
            CsTemplate.name == name,
            CsTemplate.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add_template(self, row: CsTemplate) -> CsTemplate:
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_tickets(
        self,
        *,
        shop_id: int | None,
        status: str | None,
        before_id: int | None,
        limit: int,
    ) -> list[CsTicket]:
        stmt = select(CsTicket)
        if shop_id is not None:
            stmt = stmt.where(CsTicket.shop_id == shop_id)
        if status is not None:
            stmt = stmt.where(CsTicket.status == status)
        if before_id is not None:
            stmt = stmt.where(CsTicket.id < before_id)
        stmt = stmt.order_by(CsTicket.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_ticket(self, ticket_id: int) -> CsTicket | None:
        stmt = select(CsTicket).where(CsTicket.id == ticket_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add_ticket(self, row: CsTicket) -> CsTicket:
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_notes(self, ticket_id: int) -> list[tuple[CsTicketNote, str | None]]:
        stmt = (
            select(CsTicketNote, SysUser.display_name)
            .outerjoin(SysUser, SysUser.id == CsTicketNote.created_by)
            .where(CsTicketNote.ticket_id == ticket_id)
            .order_by(CsTicketNote.created_at, CsTicketNote.id)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], None if row[1] is None else str(row[1])) for row in rows]

    async def add_note(self, row: CsTicketNote) -> CsTicketNote:
        self.session.add(row)
        await self.session.flush()
        return row

    async def assignees(self) -> list[tuple[int, str | None]]:
        stmt = (
            select(TenantUser.user_id, SysUser.display_name)
            .join(SysUser, SysUser.id == TenantUser.user_id)
            .where(
                TenantUser.deleted_at.is_(None),
                TenantUser.status == int(TenantUserStatus.ACTIVE),
            )
            .order_by(SysUser.display_name, TenantUser.user_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(int(row[0]), None if row[1] is None else str(row[1])) for row in rows]

    async def flush(self) -> None:
        await self.session.flush()
