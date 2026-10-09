"""库存台账、流水、预占、平台水位与回传日志 —— M3-06 / M3-07

流水按月分区，不软删除。可售视图用 security_invoker，避免属主绕过 RLS。
应用角色没有 DELETE。退货入库状态补上 POSTED。限流表补库存回传滞后阈值。

Revision ID: 0015_inventory_ledger
Revises: 0014_listing_locale
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_inventory_ledger"
down_revision: str | None = "0014_listing_locale"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"
_PARTITION_MONTHS_BACK = 1
_PARTITION_MONTHS_FORWARD = 12
_PLATFORM_SQL = "'amazon', 'shopee', 'lazada', 'tiktok'"
_FLOW_SQL = "'INBOUND', 'OUTBOUND', 'RESERVE', 'RELEASE', 'SHIP', 'RETURN_IN', 'ADJUST'"
_HOLD_SQL = "'OPEN', 'RELEASED', 'SHIPPED', 'SHORT'"
_PUSH_SQL = "'SUCCESS', 'FAILED', 'LAGGED_ZERO'"


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _tenant_policy(table: str) -> None:
    setting = "NULLIF(current_setting('app.current_tenant', true), '')::bigint"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id = {setting})
            WITH CHECK (tenant_id = {setting})
        """
    )


def _grant(table: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE ON {table} TO {_APP_ROLE};
                REVOKE DELETE ON {table} FROM {_APP_ROLE};
            END IF;
        END
        $$;
        """  # noqa: S608
    )


def _audit_columns() -> list[sa.Column[sa.DateTime]]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
    ]


def _create_flow_partitions() -> None:
    today = date.today().replace(day=1)
    start = _add_months(today, -_PARTITION_MONTHS_BACK)
    for i in range(_PARTITION_MONTHS_BACK + _PARTITION_MONTHS_FORWARD + 1):
        lower = _add_months(start, i)
        upper = _add_months(start, i + 1)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS inventory_flow_{lower:%Y_%m} "
            f"PARTITION OF inventory_flow FOR VALUES FROM ('{lower.isoformat()}') TO ('{upper.isoformat()}')"
        )
        _grant(f"inventory_flow_{lower:%Y_%m}")


def _drop_flow_partitions() -> None:
    today = date.today().replace(day=1)
    start = _add_months(today, -_PARTITION_MONTHS_BACK)
    for i in range(_PARTITION_MONTHS_BACK + _PARTITION_MONTHS_FORWARD + 1):
        lower = _add_months(start, i)
        op.execute(f"DROP TABLE IF EXISTS inventory_flow_{lower:%Y_%m}")


def upgrade() -> None:
    op.add_column(
        "platform_rate_limit",
        sa.Column("stock_push_lag_seconds", sa.Integer(), nullable=False, server_default="600"),
    )
    op.create_check_constraint(
        "ck_platform_rate_limit_stock_push_lag_seconds",
        "platform_rate_limit",
        "stock_push_lag_seconds > 0",
    )
    op.drop_constraint("ck_return_order_restock_status", "return_order", type_="check")
    op.create_check_constraint(
        "ck_return_order_restock_status",
        "return_order",
        "restock_status IN ('NONE', 'DEFERRED', 'POSTED')",
    )

    op.create_table(
        "warehouse",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("warehouse_type", sa.String(length=16), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("address", sa.Text(), nullable=False, server_default=""),
        sa.Column("external_code", sa.String(length=64), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_audit_columns(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "warehouse_type IN ('LOCAL', 'OVERSEAS', 'FBA', 'PLATFORM')",
            name="ck_warehouse_warehouse_type",
        ),
        sa.CheckConstraint("char_length(country) = 2", name="ck_warehouse_country"),
        sa.CheckConstraint("char_length(name) > 0", name="ck_warehouse_name"),
        sa.PrimaryKeyConstraint("id", name="pk_warehouse"),
    )
    op.create_index("ix_warehouse_tenant_id", "warehouse", ["tenant_id"])
    op.create_index("ix_warehouse_created_at", "warehouse", ["created_at"])
    op.create_index("ix_warehouse_deleted_at", "warehouse", ["deleted_at"])
    op.create_index(
        "uq_warehouse_one_default",
        "warehouse",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_warehouse_external_code_active",
        "warehouse",
        ["tenant_id", "external_code"],
        unique=True,
        postgresql_where=sa.text("external_code IS NOT NULL AND deleted_at IS NULL"),
    )
    _tenant_policy("warehouse")
    _grant("warehouse")

    op.create_table(
        "inventory",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("available", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("occupied", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("in_transit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("defective", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("safe_stock", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        *_audit_columns(),
        sa.CheckConstraint(
            "available >= 0 AND occupied >= 0 AND in_transit >= 0 AND defective >= 0 AND safe_stock >= 0",
            name="ck_inventory_non_negative",
        ),
        sa.CheckConstraint("occupied <= available", name="ck_inventory_occupied_within_available"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_inventory_sku_id_sku"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouse.id"], name="fk_inventory_warehouse_id_warehouse"),
        sa.PrimaryKeyConstraint("id", name="pk_inventory"),
        sa.UniqueConstraint("tenant_id", "sku_id", "warehouse_id", name="uq_inventory_sku_warehouse"),
    )
    op.create_index("ix_inventory_tenant_id", "inventory", ["tenant_id"])
    op.create_index("ix_inventory_created_at", "inventory", ["created_at"])
    op.create_index("ix_inventory_tenant_id_sku_id", "inventory", ["tenant_id", "sku_id"])
    _tenant_policy("inventory")
    _grant("inventory")

    op.execute(
        """
        CREATE VIEW v_sellable_inventory
        WITH (security_invoker = true) AS
        SELECT
            i.id,
            i.tenant_id,
            i.sku_id,
            i.warehouse_id,
            i.available,
            i.occupied,
            i.in_transit,
            i.defective,
            i.safe_stock,
            i.version,
            i.created_at,
            i.updated_at,
            i.created_by,
            i.updated_by,
            GREATEST(i.available - i.occupied - i.safe_stock, 0) AS sellable
        FROM inventory i
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                GRANT SELECT ON v_sellable_inventory TO {_APP_ROLE};
            END IF;
        END
        $$;
        """  # noqa: S608
    )

    op.create_table(
        "inventory_flow",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("inventory_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("flow_type", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("ref_type", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("ref_id", sa.BigInteger(), nullable=True),
        sa.Column("before_qty", sa.Integer(), nullable=False),
        sa.Column("after_qty", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(f"flow_type IN ({_FLOW_SQL})", name="ck_inventory_flow_flow_type"),
        sa.CheckConstraint("quantity >= 0", name="ck_inventory_flow_quantity"),
        sa.PrimaryKeyConstraint("id", "created_at", name="pk_inventory_flow"),
        postgresql_partition_by="RANGE (created_at)",
    )
    op.create_index("ix_inventory_flow_tenant_id", "inventory_flow", ["tenant_id"])
    op.create_index("ix_inventory_flow_tenant_id_created_at", "inventory_flow", ["tenant_id", "created_at"])
    op.create_index("ix_inventory_flow_tenant_id_ref", "inventory_flow", ["tenant_id", "ref_type", "ref_id"])
    op.create_index("ix_inventory_flow_tenant_id_sku_id", "inventory_flow", ["tenant_id", "sku_id"])
    _tenant_policy("inventory_flow")
    _grant("inventory_flow")
    _create_flow_partitions()

    op.create_table(
        "inventory_hold",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("order_item_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("short_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "allocations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(f"status IN ({_HOLD_SQL})", name="ck_inventory_hold_status"),
        sa.CheckConstraint("quantity >= 0 AND short_qty >= 0", name="ck_inventory_hold_quantity"),
        sa.CheckConstraint("jsonb_typeof(allocations) = 'array'", name="ck_inventory_hold_allocations"),
        sa.ForeignKeyConstraint(["order_id"], ["sales_order.id"], name="fk_inventory_hold_order_id_sales_order"),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_hold"),
        sa.UniqueConstraint("tenant_id", "order_item_id", name="uq_inventory_hold_order_item"),
    )
    op.create_index("ix_inventory_hold_tenant_id", "inventory_hold", ["tenant_id"])
    op.create_index("ix_inventory_hold_created_at", "inventory_hold", ["created_at"])
    op.create_index("ix_inventory_hold_tenant_id_order_id", "inventory_hold", ["tenant_id", "order_id"])
    _tenant_policy("inventory_hold")
    _grant("inventory_hold")

    op.create_table(
        "platform_safety_stock",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("cover_days", sa.Integer(), nullable=False),
        *_audit_columns(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"platform_code IN ({_PLATFORM_SQL})", name="ck_platform_safety_stock_platform_code"),
        sa.CheckConstraint(
            "quantity >= 0 AND lead_time_days >= 0 AND cover_days >= 0",
            name="ck_platform_safety_stock_non_negative",
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_platform_safety_stock_sku_id_sku"),
        sa.PrimaryKeyConstraint("id", name="pk_platform_safety_stock"),
    )
    op.create_index("ix_platform_safety_stock_tenant_id", "platform_safety_stock", ["tenant_id"])
    op.create_index("ix_platform_safety_stock_created_at", "platform_safety_stock", ["created_at"])
    op.create_index("ix_platform_safety_stock_deleted_at", "platform_safety_stock", ["deleted_at"])
    op.create_index(
        "uq_platform_safety_stock_active",
        "platform_safety_stock",
        ["tenant_id", "sku_id", "platform_code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _tenant_policy("platform_safety_stock")
    _grant("platform_safety_stock")

    op.create_table(
        "inventory_push_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.String(length=512), nullable=False, server_default=""),
        *_audit_columns(),
        sa.CheckConstraint(f"status IN ({_PUSH_SQL})", name="ck_inventory_push_log_status"),
        sa.CheckConstraint("quantity >= 0 AND retry_count >= 0", name="ck_inventory_push_log_quantity"),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_inventory_push_log_shop_id_shop"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_inventory_push_log_sku_id_sku"),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_push_log"),
    )
    op.create_index("ix_inventory_push_log_tenant_id", "inventory_push_log", ["tenant_id"])
    op.create_index("ix_inventory_push_log_created_at", "inventory_push_log", ["created_at"])
    op.create_index("ix_inventory_push_log_tenant_id_sku_id", "inventory_push_log", ["tenant_id", "sku_id"])
    op.create_index(
        "ix_inventory_push_log_tenant_shop_sku",
        "inventory_push_log",
        ["tenant_id", "shop_id", "sku_id", "created_at"],
    )
    _tenant_policy("inventory_push_log")
    _grant("inventory_push_log")


def downgrade() -> None:
    op.drop_table("inventory_push_log")
    op.drop_table("platform_safety_stock")
    op.drop_table("inventory_hold")
    _drop_flow_partitions()
    op.drop_table("inventory_flow")
    op.execute("DROP VIEW IF EXISTS v_sellable_inventory")
    op.drop_table("inventory")
    op.drop_table("warehouse")
    op.drop_constraint("ck_return_order_restock_status", "return_order", type_="check")
    op.create_check_constraint(
        "ck_return_order_restock_status",
        "return_order",
        "restock_status IN ('NONE', 'DEFERRED')",
    )
    op.drop_constraint(
        "ck_platform_rate_limit_stock_push_lag_seconds",
        "platform_rate_limit",
        type_="check",
    )
    op.drop_column("platform_rate_limit", "stock_push_lag_seconds")
