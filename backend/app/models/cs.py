"""客服消息、回复模板、售后工单（M5-05）。

消息和工单备注是同步或跟进记录，不能删除。模板是主数据，可以停用。
V1 不在站内回复买家。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, SoftDeleteMixin, TenantMixin
from app.engines.cs import MESSAGE_STATUSES, TEMPLATE_SCENES, TICKET_STATUSES, TICKET_TYPES

_MESSAGE_SQL = ", ".join(f"'{item}'" for item in MESSAGE_STATUSES)
_SCENE_SQL = ", ".join(f"'{item}'" for item in TEMPLATE_SCENES)
_TICKET_STATUS_SQL = ", ".join(f"'{item}'" for item in TICKET_STATUSES)
_TICKET_TYPE_SQL = ", ".join(f"'{item}'" for item in TICKET_TYPES)


class CsMessage(Base, PKMixin, TenantMixin, AuditMixin):
    """平台买家消息。同一店铺的平台消息号只保留一行。"""

    __tablename__ = "cs_message"

    shop_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shop.id"), nullable=False)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    platform_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sales_order.id"), nullable=True)
    buyer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    sla_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    console_url: Mapped[str] = mapped_column(String(512), nullable=False)

    __table_args__ = (
        CheckConstraint(f"status IN ({_MESSAGE_SQL})", name="status"),
        CheckConstraint("char_length(btrim(platform_message_id)) > 0", name="platform_message_id"),
        CheckConstraint("char_length(btrim(content)) > 0", name="content"),
        CheckConstraint("console_url LIKE 'https://%'", name="console_url"),
        Index(
            "uq_cs_message_shop_platform_message",
            "tenant_id",
            "shop_id",
            "platform_message_id",
            unique=True,
        ),
        Index("ix_cs_message_tenant_id_sla_deadline", "tenant_id", "sla_deadline"),
        Index("ix_cs_message_tenant_id_status", "tenant_id", "status"),
    )


class CsTemplate(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """回复模板。按场景和语言维护，渲染后由客服复制到平台后台。"""

    __tablename__ = "cs_template"

    scene: Mapped[str] = mapped_column(String(16), nullable=False)
    lang: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(f"scene IN ({_SCENE_SQL})", name="scene"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name"),
        CheckConstraint("char_length(btrim(body)) > 0", name="body"),
        Index(
            "uq_cs_template_live",
            "tenant_id",
            "scene",
            "lang",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class CsTicket(Base, PKMixin, TenantMixin, AuditMixin):
    """售后工单。关闭后保留，不删除。"""

    __tablename__ = "cs_ticket"

    shop_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shop.id"), nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cs_message.id"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sales_order.id"), nullable=True)
    return_order_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("return_order.id"), nullable=True)
    buyer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ticket_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    assignee_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(f"ticket_type IN ({_TICKET_TYPE_SQL})", name="ticket_type"),
        CheckConstraint(f"status IN ({_TICKET_STATUS_SQL})", name="status"),
        CheckConstraint("char_length(btrim(title)) > 0", name="title"),
        Index("ix_cs_ticket_tenant_id_status", "tenant_id", "status"),
        Index("ix_cs_ticket_tenant_id_order_id", "tenant_id", "order_id"),
    )


class CsTicketNote(Base, PKMixin, TenantMixin, AuditMixin):
    """工单跟进。只追加，不修改，不删除。"""

    __tablename__ = "cs_ticket_note"

    ticket_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cs_ticket.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("char_length(btrim(body)) > 0", name="body"),
        Index("ix_cs_ticket_note_tenant_id_ticket_id_created_at", "tenant_id", "ticket_id", "created_at"),
    )


__all__ = ["CsMessage", "CsTemplate", "CsTicket", "CsTicketNote"]
