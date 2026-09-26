"""订单域（PRD 9.2 ④，Alembic 迁移批次 3 的第一截）。

``sales_order`` 与 ``order_item`` 是流水，禁止软删除。
``unified_status`` 先存适配器给出的统一状态文案。九态状态机和可配置映射在 M2-06。
``order_item`` 按月分区，主键包含 ``created_at``。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, TenantMixin
from app.db.snowflake import next_snowflake_id

ORDER_MODULE = "order"


class SalesOrder(Base, PKMixin, TenantMixin, AuditMixin):
    """订单主表。幂等键 ``{platform}:{shop_id}:{platform_order_id}`` 在租户内唯一。"""

    __tablename__ = "sales_order"

    shop_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shop.id"), nullable=False)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    platform_status: Mapped[str] = mapped_column(String(64), nullable=False)
    unified_status: Mapped[str] = mapped_column(String(32), nullable=False)
    buyer_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ship_to: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    item_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    shipping_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    platform_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_sales_order_tenant_id_idempotency_key"),
        Index("ix_sales_order_tenant_id_unified_status_created_at", "tenant_id", "unified_status", "created_at"),
        Index("ix_sales_order_tenant_id_shop_id_created_at", "tenant_id", "shop_id", "created_at"),
    )


class OrderItem(Base, TenantMixin):
    """订单明细，按创建月份分区。同步更新时替换该订单的当前明细，不软删主单。"""

    __tablename__ = "order_item"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=next_snowflake_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_order.id"), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    listing_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    platform_sku_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    platform_product_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    item_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at", name="pk_order_item"),
        Index("ix_order_item_tenant_id_order_id", "tenant_id", "order_id"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )


class ShopSyncCursor(Base, PKMixin, TenantMixin, AuditMixin):
    """店铺同步高水位。``resume_cursor`` 非空时表示上一轮分页还没拉完。"""

    __tablename__ = "shop_sync_cursor"

    shop_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shop.id"), nullable=False)
    module: Mapped[str] = mapped_column(String(32), nullable=False)
    cursor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resume_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resume_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "shop_id", "module", name="uq_shop_sync_cursor_tenant_id_shop_id_module"),
        Index("ix_shop_sync_cursor_tenant_id_module_cursor_at", "tenant_id", "module", "cursor_at"),
    )


__all__ = ["ORDER_MODULE", "OrderItem", "SalesOrder", "ShopSyncCursor"]
