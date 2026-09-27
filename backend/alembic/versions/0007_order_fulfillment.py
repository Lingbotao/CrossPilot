"""订单工作台 —— M2-08 / M2-09

``shipment`` 与 ``order_fee`` 是租户表，补 RLS。
发货单不允许应用角色删除。费用行在同步时整单替换，所以保留 DELETE。
列表覆盖索引带 INCLUDE，搜索另加 trigram 与手机号后四位。

Revision ID: 0007_order_fulfillment
Revises: 0006_order_status
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007_order_fulfillment"
down_revision: str | None = "0006_order_status"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"


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


def upgrade() -> None:
    op.create_table(
        "shipment",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("carrier", sa.String(length=64), nullable=False),
        sa.Column("tracking_no", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_shipment"),
        sa.ForeignKeyConstraint(["order_id"], ["sales_order.id"], name="fk_shipment_order_id_sales_order"),
        sa.UniqueConstraint("tenant_id", "order_id", name="uq_shipment_tenant_id_order_id"),
        sa.CheckConstraint("status IN ('SUCCEEDED', 'FAILED')", name="ck_shipment_status"),
    )
    op.create_index("ix_shipment_tenant_id", "shipment", ["tenant_id"])
    op.create_index("ix_shipment_created_at", "shipment", ["created_at"])
    op.create_index("ix_shipment_tenant_id_status", "shipment", ["tenant_id", "status"])
    _exec_script(*_tenant_policy_sql("shipment"))

    op.create_table(
        "order_fee",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("fee_type", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="platform"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_order_fee"),
        sa.ForeignKeyConstraint(["order_id"], ["sales_order.id"], name="fk_order_fee_order_id_sales_order"),
        sa.CheckConstraint("source IN ('platform', 'system')", name="ck_order_fee_source"),
    )
    op.create_index("ix_order_fee_tenant_id", "order_fee", ["tenant_id"])
    op.create_index("ix_order_fee_created_at", "order_fee", ["created_at"])
    op.create_index("ix_order_fee_tenant_id_order_id", "order_fee", ["tenant_id", "order_id"])
    _exec_script(*_tenant_policy_sql("order_fee"))

    op.create_index(
        "ix_sales_order_list_cover",
        "sales_order",
        ["tenant_id", "unified_status", "created_at", "id"],
        postgresql_include=["shop_id", "platform_code", "platform_order_id", "currency", "total_amount"],
    )
    op.create_index(
        "ix_order_item_tenant_id_platform_sku_id",
        "order_item",
        ["tenant_id", "platform_sku_id"],
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sales_order_platform_order_id_trgm "
        "ON sales_order USING gin (platform_order_id gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sales_order_buyer_name_trgm "
        "ON sales_order USING gin ((buyer_info->>'name') gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sales_order_tenant_phone_last4 "
        "ON sales_order (tenant_id, (buyer_info->>'phone_last4'))"
    )

    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE ON shipment TO {_APP_ROLE};
                REVOKE DELETE ON shipment FROM {_APP_ROLE};
                GRANT SELECT, INSERT, UPDATE, DELETE ON order_fee TO {_APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sales_order_tenant_phone_last4")
    op.execute("DROP INDEX IF EXISTS ix_sales_order_buyer_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_sales_order_platform_order_id_trgm")
    op.drop_index("ix_order_item_tenant_id_platform_sku_id", table_name="order_item")
    op.drop_index("ix_sales_order_list_cover", table_name="sales_order")
    op.drop_table("order_fee")
    op.drop_table("shipment")
