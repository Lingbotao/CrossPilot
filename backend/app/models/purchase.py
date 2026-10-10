"""采购与头程（M5-01 / M5-02）。

供应商和 SKU 供应关系是主数据，可软删除。
采购单、收货、头程、分摊和成本池是账本，不软删除。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, SoftDeleteMixin, TenantMixin
from app.engines.landed_cost import CHANNELS
from app.engines.purchase import ALLOC_METHODS, PO_STATUSES, SETTLEMENT_TYPES, SHIPMENT_STATUSES
from app.models.locale import CONTENT_MARKETS

REAL_CHANNELS: tuple[str, ...] = tuple(item for item in CHANNELS if item != "*")
_STATUS_SQL = ", ".join(f"'{item}'" for item in PO_STATUSES)
_SETTLEMENT_SQL = ", ".join(f"'{item}'" for item in SETTLEMENT_TYPES)
_CHANNEL_SQL = ", ".join(f"'{item}'" for item in REAL_CHANNELS)
_ALLOC_SQL = ", ".join(f"'{item}'" for item in ALLOC_METHODS)
_SHIPMENT_SQL = ", ".join(f"'{item}'" for item in SHIPMENT_STATUSES)
_MARKET_SQL = ", ".join(f"'{item}'" for item in CONTENT_MARKETS)


class Supplier(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """供应商。评级是手工分，自动评分不在本里程碑。"""

    __tablename__ = "supplier"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    contact: Mapped[str] = mapped_column(String(256), nullable=False, default="", server_default="")
    settlement_type: Mapped[str] = mapped_column(String(16), nullable=False)
    credit_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        CheckConstraint(f"settlement_type IN ({_SETTLEMENT_SQL})", name="settlement_type"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name"),
        CheckConstraint("credit_days >= 0", name="credit_days"),
        CheckConstraint("rating IS NULL OR (rating >= 1 AND rating <= 5)", name="rating"),
        CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="idempotency_key",
        ),
        Index(
            "uq_supplier_name_active",
            "tenant_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_supplier_idempotency",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
        ),
    )


class SkuSupplier(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """一个 SKU 可以有多个供应商，未删除行里只能有一个默认供应商。"""

    __tablename__ = "sku_supplier"

    supplier_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))

    __table_args__ = (
        ForeignKeyConstraint(["supplier_id"], ["supplier.id"], name="fk_sku_supplier_supplier_id_supplier"),
        ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_sku_supplier_sku_id_sku"),
        Index(
            "uq_sku_supplier_pair_active",
            "tenant_id",
            "supplier_id",
            "sku_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_sku_supplier_default",
            "tenant_id",
            "sku_id",
            unique=True,
            postgresql_where=text("is_default AND deleted_at IS NULL"),
        ),
        Index("ix_sku_supplier_tenant_id_supplier_id", "tenant_id", "supplier_id"),
    )


class PurchaseOrder(Base, PKMixin, TenantMixin, AuditMixin):
    """采购单。审核通过后才记在途。"""

    __tablename__ = "purchase_order"

    supplier_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT", server_default="DRAFT")
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    expected_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["supplier_id"], ["supplier.id"], name="fk_purchase_order_supplier_id_supplier"),
        ForeignKeyConstraint(["warehouse_id"], ["warehouse.id"], name="fk_purchase_order_warehouse_id_warehouse"),
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="status"),
        CheckConstraint("total_amount >= 0", name="total_amount"),
        CheckConstraint("char_length(currency) = 3", name="currency"),
        CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="idempotency_key",
        ),
        Index("uq_purchase_order_idempotency", "tenant_id", "idempotency_key", unique=True),
        Index("ix_purchase_order_tenant_id_status", "tenant_id", "status"),
    )


class PurchaseOrderItem(Base, PKMixin, TenantMixin, AuditMixin):
    """采购明细。超收时已收数量可以大于订购数量。"""

    __tablename__ = "purchase_order_item"

    purchase_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    received_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    short_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    tax_included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    expected_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_order.id"],
            name="fk_purchase_order_item_purchase_order_id_purchase_order",
        ),
        ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_purchase_order_item_sku_id_sku"),
        CheckConstraint("quantity > 0 AND received_qty >= 0 AND short_qty >= 0", name="quantity"),
        CheckConstraint("unit_price >= 0", name="unit_price"),
        CheckConstraint("char_length(currency) = 3", name="currency"),
        Index(
            "uq_purchase_order_item_sku",
            "tenant_id",
            "purchase_order_id",
            "sku_id",
            unique=True,
        ),
        Index("ix_purchase_order_item_tenant_id_purchase_order_id", "tenant_id", "purchase_order_id"),
    )


class PurchaseReceipt(Base, PKMixin, TenantMixin, AuditMixin):
    """一次收货或短收。行明细留在 lines，库存侧另有流水。"""

    __tablename__ = "purchase_receipt"

    purchase_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_order.id"],
            name="fk_purchase_receipt_purchase_order_id_purchase_order",
        ),
        CheckConstraint("disposition IN ('RECEIVE', 'SHORT')", name="disposition"),
        CheckConstraint("jsonb_typeof(lines) = 'array'", name="lines"),
        CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="idempotency_key",
        ),
        Index("uq_purchase_receipt_idempotency", "tenant_id", "idempotency_key", unique=True),
        Index("ix_purchase_receipt_tenant_id_purchase_order_id", "tenant_id", "purchase_order_id"),
    )


class FirstMileShipment(Base, PKMixin, TenantMixin, AuditMixin):
    """头程物流单。过账后写分摊和成本池，草稿只保存明细。"""

    __tablename__ = "first_mile_shipment"

    purchase_order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    from_warehouse_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    to_warehouse_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    forwarder: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    container_no: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    destination_market: Mapped[str] = mapped_column(String(2), nullable=False)
    cost_total: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    alloc_method: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT", server_default="DRAFT")
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_order.id"],
            name="fk_first_mile_shipment_purchase_order_id_purchase_order",
        ),
        ForeignKeyConstraint(
            ["from_warehouse_id"],
            ["warehouse.id"],
            name="fk_first_mile_shipment_from_warehouse_id_warehouse",
        ),
        ForeignKeyConstraint(
            ["to_warehouse_id"],
            ["warehouse.id"],
            name="fk_first_mile_shipment_to_warehouse_id_warehouse",
        ),
        CheckConstraint(f"channel IN ({_CHANNEL_SQL})", name="channel"),
        CheckConstraint(f"destination_market IN ({_MARKET_SQL})", name="destination_market"),
        CheckConstraint(f"alloc_method IN ({_ALLOC_SQL})", name="alloc_method"),
        CheckConstraint(f"status IN ({_SHIPMENT_SQL})", name="status"),
        CheckConstraint("cost_total > 0", name="cost_total"),
        CheckConstraint("char_length(currency) = 3", name="currency"),
        CheckConstraint("char_length(btrim(forwarder)) > 0", name="forwarder"),
        CheckConstraint("storage_days >= 0", name="storage_days"),
        CheckConstraint("jsonb_typeof(lines) = 'array'", name="lines"),
        CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="idempotency_key",
        ),
        Index("uq_first_mile_shipment_idempotency", "tenant_id", "idempotency_key", unique=True),
        Index("ix_first_mile_shipment_tenant_id_status", "tenant_id", "status"),
    )


class FirstMileCostAllocation(Base, PKMixin, TenantMixin, AuditMixin):
    """头程费用分摊。各行金额之和等于头程总额。"""

    __tablename__ = "first_mile_cost_allocation"

    shipment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["shipment_id"],
            ["first_mile_shipment.id"],
            name="fk_first_mile_cost_allocation_shipment_id_first_mile_shipment",
        ),
        ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_first_mile_cost_allocation_sku_id_sku"),
        CheckConstraint(f"method IN ({_ALLOC_SQL})", name="method"),
        CheckConstraint("quantity > 0 AND allocated_cost >= 0", name="quantity"),
        CheckConstraint("char_length(currency) = 3", name="currency"),
        CheckConstraint("char_length(btrim(formula)) > 0 AND char_length(btrim(source)) > 0", name="explain"),
        Index("uq_first_mile_cost_allocation_sku", "tenant_id", "shipment_id", "sku_id", unique=True),
        Index("ix_first_mile_cost_allocation_tenant_id_shipment_id", "tenant_id", "shipment_id"),
    )


class SkuCostPool(Base, PKMixin, TenantMixin, AuditMixin):
    """到仓单位成本。只追加，当前成本取该 SKU 最新一行。"""

    __tablename__ = "sku_cost_pool"

    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shipment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purchase_order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    purchase_unit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    first_mile_unit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    duty_unit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    import_tax_unit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    brokerage_unit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    storage_unit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    fx_reserve_unit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    landed_unit: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_sku_cost_pool_sku_id_sku"),
        ForeignKeyConstraint(
            ["shipment_id"],
            ["first_mile_shipment.id"],
            name="fk_sku_cost_pool_shipment_id_first_mile_shipment",
        ),
        ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_order.id"],
            name="fk_sku_cost_pool_purchase_order_id_purchase_order",
        ),
        CheckConstraint("quantity > 0", name="quantity"),
        CheckConstraint("char_length(currency) = 3", name="currency"),
        CheckConstraint(
            "purchase_unit >= 0 AND first_mile_unit >= 0 AND duty_unit >= 0 AND import_tax_unit >= 0"
            " AND brokerage_unit >= 0 AND storage_unit >= 0 AND fx_reserve_unit >= 0 AND landed_unit >= 0",
            name="units",
        ),
        CheckConstraint("jsonb_typeof(lines) = 'array'", name="lines"),
        CheckConstraint("char_length(btrim(source)) > 0", name="source"),
        Index("ix_sku_cost_pool_tenant_id_sku_id", "tenant_id", "sku_id"),
    )
