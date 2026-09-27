"""订单域（PRD 9.2 ④）。

``sales_order``、``order_item``、``order_status_log``、``order_fee`` 都是流水，禁止软删除。
``shipment`` 按订单覆盖最新一次发货结果，同样不软删除。
``unified_status`` 只允许九态文案（见 ``app.engines.order_status``）。
``order_item`` 与 ``order_status_log`` 按月分区，主键包含 ``created_at``。

列表覆盖索引、买家名 trigram、手机号后四位表达式索引写在迁移 ``0007`` 里。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, TenantMixin
from app.db.snowflake import next_snowflake_id
from app.engines.order_desk import RESTOCK_STATUS_SQL, RETURN_STATUS_SQL, REVIEW_STATUS_SQL
from app.engines.order_status import UNIFIED_STATUS_SQL

ORDER_MODULE = "order"
SHIPMENT_SUCCEEDED = "SUCCEEDED"
SHIPMENT_FAILED = "FAILED"


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
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="AUTO_PASSED", server_default="AUTO_PASSED"
    )
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_sales_order_tenant_id_idempotency_key"),
        CheckConstraint(
            f"unified_status IN ({UNIFIED_STATUS_SQL})",
            name="unified_status",
        ),
        CheckConstraint(
            f"review_status IN ({REVIEW_STATUS_SQL})",
            name="review_status",
        ),
        Index("ix_sales_order_tenant_id_unified_status_created_at", "tenant_id", "unified_status", "created_at"),
        Index("ix_sales_order_tenant_id_shop_id_created_at", "tenant_id", "shop_id", "created_at"),
        Index(
            "ix_sales_order_list_cover",
            "tenant_id",
            "unified_status",
            "created_at",
            "id",
            postgresql_include=["shop_id", "platform_code", "platform_order_id", "currency", "total_amount"],
        ),
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
        Index("ix_order_item_tenant_id_platform_sku_id", "tenant_id", "platform_sku_id"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )


class OrderStatusLog(Base, TenantMixin):
    """状态变更日志。只追加，不更新、不删除。按创建月份分区。"""

    __tablename__ = "order_status_log"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=next_snowflake_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_order.id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_status: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    operator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at", name="pk_order_status_log"),
        CheckConstraint(f"to_status IN ({UNIFIED_STATUS_SQL})", name="to_status"),
        CheckConstraint(
            f"from_status IS NULL OR from_status IN ({UNIFIED_STATUS_SQL})",
            name="from_status",
        ),
        CheckConstraint(
            "source IN ('SYSTEM', 'WEBHOOK', 'MANUAL')",
            name="source",
        ),
        Index("ix_order_status_log_tenant_id_order_id_created_at", "tenant_id", "order_id", "created_at"),
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


class Shipment(Base, PKMixin, TenantMixin, AuditMixin):
    """发货单。一个订单一行，失败重试时覆盖，不另开流水。"""

    __tablename__ = "shipment"

    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_order.id"), nullable=False)
    carrier: Mapped[str] = mapped_column(String(64), nullable=False)
    tracking_no: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("tenant_id", "order_id", name="uq_shipment_tenant_id_order_id"),
        CheckConstraint(
            f"status IN ('{SHIPMENT_SUCCEEDED}', '{SHIPMENT_FAILED}')",
            name="status",
        ),
        Index("ix_shipment_tenant_id_status", "tenant_id", "status"),
    )


class OrderFee(Base, PKMixin, TenantMixin, AuditMixin):
    """订单费用行。同步时整单替换，主单不软删。"""

    __tablename__ = "order_fee"

    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_order.id"), nullable=False)
    fee_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="platform")

    __table_args__ = (
        CheckConstraint("source IN ('platform', 'system')", name="source"),
        Index("ix_order_fee_tenant_id_order_id", "tenant_id", "order_id"),
    )


class OrderReviewRule(Base, PKMixin, TenantMixin, AuditMixin):
    """金额审核规则。同一租户同一币种一条。超过阈值才进入人工审核。"""

    __tablename__ = "order_review_rule"

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_gt: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))

    __table_args__ = (UniqueConstraint("tenant_id", "currency", name="uq_order_review_rule_tenant_id_currency"),)


class OrderNote(Base, PKMixin, TenantMixin):
    """订单备注。回传平台成功后才追加，不更新、不删除。"""

    __tablename__ = "order_note"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_order.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (Index("ix_order_note_tenant_id_order_id_created_at", "tenant_id", "order_id", "created_at"),)


class OrderAddressLog(Base, PKMixin, TenantMixin):
    """改址历史。失败行保留，但不会改订单上的收货地址。"""

    __tablename__ = "order_address_log"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_order.id"), nullable=False)
    before_address: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_address: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('SUCCEEDED', 'FAILED')", name="status"),
        Index("ix_order_address_log_tenant_id_order_id_created_at", "tenant_id", "order_id", "created_at"),
    )


class ReturnOrder(Base, PKMixin, TenantMixin, AuditMixin):
    """退货退款单。库存恢复记在 restock_status，等库存模块入账。"""

    __tablename__ = "return_order"

    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sales_order.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    restock_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    restock_sellable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    restock_status: Mapped[str] = mapped_column(String(16), nullable=False, default="NONE", server_default="NONE")

    __table_args__ = (
        CheckConstraint(f"status IN ({RETURN_STATUS_SQL})", name="status"),
        CheckConstraint(f"restock_status IN ({RESTOCK_STATUS_SQL})", name="restock_status"),
        Index("ix_return_order_tenant_id_order_id", "tenant_id", "order_id"),
        Index("ix_return_order_tenant_id_status", "tenant_id", "status"),
    )


__all__ = [
    "ORDER_MODULE",
    "SHIPMENT_FAILED",
    "SHIPMENT_SUCCEEDED",
    "OrderAddressLog",
    "OrderFee",
    "OrderItem",
    "OrderNote",
    "OrderReviewRule",
    "OrderStatusLog",
    "ReturnOrder",
    "SalesOrder",
    "Shipment",
    "ShopSyncCursor",
]
