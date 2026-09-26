"""订单域 —— Alembic 迁移批次 3 的第一截（M2-05）

创建 sales_order / order_item / shop_sync_cursor。
``order_item`` 建表即按月分区。三张表都是租户表，补 RLS。
订单主表是流水，应用角色不能 DELETE。明细替换需要 DELETE。

Revision ID: 0005_order_sync
Revises: 0004_platform_auth_domain
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_order_sync"
down_revision: str | None = "0004_platform_auth_domain"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"
_PARTITION_MONTHS_BACK = 1
_PARTITION_MONTHS_FORWARD = 12
_TENANT_TABLES = ("sales_order", "order_item", "shop_sync_cursor")


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _exec_script(*statements: str) -> None:
    for stmt in statements:
        op.execute(stmt)


def _tenant_policy_sql(table: str) -> list[str]:
    setting = "NULLIF(current_setting('app.current_tenant', true), '')::bigint"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id = {setting})
            WITH CHECK (tenant_id = {setting})
        """,
    ]


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "sales_order",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("platform_order_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("platform_status", sa.String(length=64), nullable=False),
        sa.Column("unified_status", sa.String(length=32), nullable=False),
        sa.Column("buyer_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ship_to", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("item_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("shipping_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("tax_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("discount_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("total_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("platform_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_sales_order"),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_sales_order_shop_id_shop"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_sales_order_tenant_id_idempotency_key"),
    )
    op.create_index("ix_sales_order_tenant_id", "sales_order", ["tenant_id"])
    op.create_index("ix_sales_order_created_at", "sales_order", ["created_at"])
    op.create_index(
        "ix_sales_order_tenant_id_unified_status_created_at",
        "sales_order",
        ["tenant_id", "unified_status", "created_at"],
    )
    op.create_index(
        "ix_sales_order_tenant_id_shop_id_created_at",
        "sales_order",
        ["tenant_id", "shop_id", "created_at"],
    )

    op.create_table(
        "order_item",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=True),
        sa.Column("listing_id", sa.BigInteger(), nullable=True),
        sa.Column("platform_sku_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("platform_product_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("item_name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.PrimaryKeyConstraint("id", "created_at", name="pk_order_item"),
        sa.ForeignKeyConstraint(["order_id"], ["sales_order.id"], name="fk_order_item_order_id_sales_order"),
        postgresql_partition_by="RANGE (created_at)",
    )
    op.create_index("ix_order_item_tenant_id", "order_item", ["tenant_id"])
    op.create_index("ix_order_item_tenant_id_order_id", "order_item", ["tenant_id", "order_id"])
    _create_order_item_partitions()

    op.create_table(
        "shop_sync_cursor",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("cursor_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resume_cursor", sa.Text(), nullable=True),
        sa.Column("resume_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resume_until", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_shop_sync_cursor"),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_shop_sync_cursor_shop_id_shop"),
        sa.UniqueConstraint(
            "tenant_id",
            "shop_id",
            "module",
            name="uq_shop_sync_cursor_tenant_id_shop_id_module",
        ),
    )
    op.create_index("ix_shop_sync_cursor_tenant_id", "shop_sync_cursor", ["tenant_id"])
    op.create_index("ix_shop_sync_cursor_created_at", "shop_sync_cursor", ["created_at"])
    op.create_index(
        "ix_shop_sync_cursor_tenant_id_module_cursor_at",
        "shop_sync_cursor",
        ["tenant_id", "module", "cursor_at"],
    )

    for table in _TENANT_TABLES:
        _exec_script(*_tenant_policy_sql(table))

    op.execute(
        f"""
        DO $$
        DECLARE
            part regclass;
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE ON sales_order, shop_sync_cursor TO {_APP_ROLE};
                GRANT SELECT, INSERT, UPDATE, DELETE ON order_item TO {_APP_ROLE};
                FOR part IN
                    SELECT inhrelid
                    FROM pg_inherits
                    WHERE inhparent = 'order_item'::regclass
                LOOP
                    EXECUTE format(
                        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %s TO {_APP_ROLE}',
                        part
                    );
                END LOOP;
            END IF;
        END
        $$;
        """
    )


def _create_order_item_partitions() -> None:
    today = date.today().replace(day=1)
    start = _add_months(today, -_PARTITION_MONTHS_BACK)
    for i in range(_PARTITION_MONTHS_BACK + _PARTITION_MONTHS_FORWARD + 1):
        lower = _add_months(start, i)
        upper = _add_months(start, i + 1)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS order_item_{lower:%Y_%m} "
            f"PARTITION OF order_item FOR VALUES FROM ('{lower.isoformat()}') TO ('{upper.isoformat()}')"
        )


def downgrade() -> None:
    today = date.today().replace(day=1)
    start = _add_months(today, -_PARTITION_MONTHS_BACK)
    for i in range(_PARTITION_MONTHS_BACK + _PARTITION_MONTHS_FORWARD + 1):
        lower = _add_months(start, i)
        op.execute(f"DROP TABLE IF EXISTS order_item_{lower:%Y_%m}")
    op.drop_table("order_item")
    op.drop_table("shop_sync_cursor")
    op.drop_table("sales_order")
