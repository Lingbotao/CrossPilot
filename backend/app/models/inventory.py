"""库存与仓储（M3-06 / M3-07）。

``warehouse`` 和平台水位是主数据，可软删除。
``inventory`` 是余额，``inventory_flow`` / ``inventory_hold`` / ``inventory_push_log`` 是流水，不软删除。
可售口径在数据库视图 ``v_sellable_inventory``，不在这张余额表上再减一次平台水位。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.registry import SUPPORTED_PLATFORMS
from app.db.base import AuditMixin, Base, PKMixin, SoftDeleteMixin, TenantMixin
from app.db.snowflake import next_snowflake_id

WAREHOUSE_TYPES: tuple[str, ...] = ("LOCAL", "OVERSEAS", "FBA", "PLATFORM")
WAREHOUSE_TYPE_SQL = ", ".join(f"'{item}'" for item in WAREHOUSE_TYPES)
FLOW_TYPES: tuple[str, ...] = (
    "INBOUND",
    "OUTBOUND",
    "RESERVE",
    "RELEASE",
    "SHIP",
    "RETURN_IN",
    "ADJUST",
)
FLOW_TYPE_SQL = ", ".join(f"'{item}'" for item in FLOW_TYPES)
HOLD_STATUSES: tuple[str, ...] = ("OPEN", "RELEASED", "SHIPPED", "SHORT")
HOLD_STATUS_SQL = ", ".join(f"'{item}'" for item in HOLD_STATUSES)
PUSH_STATUSES: tuple[str, ...] = ("SUCCESS", "FAILED", "LAGGED_ZERO")
PUSH_STATUS_SQL = ", ".join(f"'{item}'" for item in PUSH_STATUSES)
PLATFORM_SQL = ", ".join(f"'{item}'" for item in SUPPORTED_PLATFORMS)
DEFAULT_SAFE_STOCK = 2


class Warehouse(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """仓库。每租户最多一个未删除的默认仓。"""

    __tablename__ = "warehouse"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    warehouse_type: Mapped[str] = mapped_column(String(16), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    external_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))

    __table_args__ = (
        CheckConstraint(f"warehouse_type IN ({WAREHOUSE_TYPE_SQL})", name="warehouse_type"),
        CheckConstraint("char_length(country) = 2", name="country"),
        CheckConstraint("char_length(name) > 0", name="name"),
        Index(
            "uq_warehouse_one_default",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_default AND deleted_at IS NULL"),
        ),
        Index(
            "uq_warehouse_external_code_active",
            "tenant_id",
            "external_code",
            unique=True,
            postgresql_where=text("external_code IS NOT NULL AND deleted_at IS NULL"),
        ),
    )


class Inventory(Base, PKMixin, TenantMixin, AuditMixin):
    """SKU × 仓库余额。乐观锁 ``version``，预占不得超过实物。"""

    __tablename__ = "inventory"

    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    available: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    occupied: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    in_transit: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    defective: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    safe_stock: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_SAFE_STOCK, server_default=text(str(DEFAULT_SAFE_STOCK))
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    __table_args__ = (
        ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_inventory_sku_id_sku"),
        ForeignKeyConstraint(["warehouse_id"], ["warehouse.id"], name="fk_inventory_warehouse_id_warehouse"),
        CheckConstraint(
            "available >= 0 AND occupied >= 0 AND in_transit >= 0 AND defective >= 0 AND safe_stock >= 0",
            name="non_negative",
        ),
        CheckConstraint("occupied <= available", name="occupied_within_available"),
        Index("uq_inventory_sku_warehouse", "tenant_id", "sku_id", "warehouse_id", unique=True),
        Index("ix_inventory_tenant_id_sku_id", "tenant_id", "sku_id"),
    )


class InventoryFlow(Base, TenantMixin):
    """库存流水。按创建月份分区，只追加。"""

    __tablename__ = "inventory_flow"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=next_snowflake_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    inventory_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    flow_type: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    ref_type: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    ref_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    before_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    after_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at", name="pk_inventory_flow"),
        CheckConstraint(f"flow_type IN ({FLOW_TYPE_SQL})", name="flow_type"),
        CheckConstraint("quantity >= 0", name="quantity"),
        Index("ix_inventory_flow_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_inventory_flow_tenant_id_ref", "tenant_id", "ref_type", "ref_id"),
        Index("ix_inventory_flow_tenant_id_sku_id", "tenant_id", "sku_id"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )


class InventoryHold(Base, PKMixin, TenantMixin, AuditMixin):
    """一笔订单明细的预占。分区流水做不了跨月唯一，幂等落在这里。"""

    __tablename__ = "inventory_hold"

    order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    short_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    allocations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["order_id"], ["sales_order.id"], name="fk_inventory_hold_order_id_sales_order"),
        CheckConstraint(f"status IN ({HOLD_STATUS_SQL})", name="status"),
        CheckConstraint("quantity >= 0 AND short_qty >= 0", name="quantity"),
        CheckConstraint("jsonb_typeof(allocations) = 'array'", name="allocations"),
        Index("uq_inventory_hold_order_item", "tenant_id", "order_item_id", unique=True),
        Index("ix_inventory_hold_tenant_id_order_id", "tenant_id", "order_id"),
    )


class PlatformSafetyStock(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """SKU × 平台的安全水位、交期和覆盖天数。补货建议只读这三列。"""

    __tablename__ = "platform_safety_stock"

    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_SAFE_STOCK, server_default=text(str(DEFAULT_SAFE_STOCK))
    )
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    cover_days: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_platform_safety_stock_sku_id_sku"),
        CheckConstraint(f"platform_code IN ({PLATFORM_SQL})", name="platform_code"),
        CheckConstraint("quantity >= 0 AND lead_time_days >= 0 AND cover_days >= 0", name="non_negative"),
        Index(
            "uq_platform_safety_stock_active",
            "tenant_id",
            "sku_id",
            "platform_code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class InventoryPushLog(Base, PKMixin, TenantMixin, AuditMixin):
    """库存回传日志。失败和滞后降 0 都留在这里，不软删除。"""

    __tablename__ = "inventory_push_log"

    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    message: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")

    __table_args__ = (
        ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_inventory_push_log_shop_id_shop"),
        ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_inventory_push_log_sku_id_sku"),
        CheckConstraint(f"status IN ({PUSH_STATUS_SQL})", name="status"),
        CheckConstraint("quantity >= 0 AND retry_count >= 0", name="quantity"),
        Index("ix_inventory_push_log_tenant_id_sku_id", "tenant_id", "sku_id"),
        Index("ix_inventory_push_log_tenant_shop_sku", "tenant_id", "shop_id", "sku_id", "created_at"),
    )


__all__ = [
    "DEFAULT_SAFE_STOCK",
    "FLOW_TYPES",
    "HOLD_STATUSES",
    "PUSH_STATUSES",
    "WAREHOUSE_TYPES",
    "Inventory",
    "InventoryFlow",
    "InventoryHold",
    "InventoryPushLog",
    "PlatformSafetyStock",
    "Warehouse",
]
