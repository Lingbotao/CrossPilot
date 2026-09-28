"""商品主数据 SPU/SKU —— M3-01

主数据可软删除。应用角色没有 DELETE，避免硬删。
SKU 编码唯一索引只覆盖未删除行。RLS 手写，autogenerate 不会生成策略。

Revision ID: 0010_product_master
Revises: 0009_platform_rate_limit
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010_product_master"
down_revision: str | None = "0009_platform_rate_limit"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"
_STATUS_SQL = "status IN ('DRAFT', 'ON_SALE', 'STOPPED', 'OUT_OF_STOCK', 'VIOLATION_OFF')"


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


def _grant_master(table: str) -> None:
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


def upgrade() -> None:
    op.create_table(
        "spu",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("brand", sa.String(length=128), nullable=True),
        sa.Column("material", sa.String(length=128), nullable=True),
        sa.Column("purpose", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_STATUS_SQL, name="ck_spu_status"),
        sa.PrimaryKeyConstraint("id", name="pk_spu"),
    )
    op.create_index("ix_spu_tenant_id", "spu", ["tenant_id"])
    op.create_index("ix_spu_created_at", "spu", ["created_at"])
    op.create_index("ix_spu_deleted_at", "spu", ["deleted_at"])
    op.create_index("ix_spu_tenant_id_status", "spu", ["tenant_id", "status"])
    _tenant_policy("spu")
    _grant_master("spu")

    op.create_table(
        "sku",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("spu_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_code", sa.String(length=64), nullable=False),
        sa.Column("barcode", sa.String(length=64), nullable=True),
        sa.Column("spec_attrs", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("weight_g", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("length_cm", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("width_cm", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("height_cm", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("purchase_price", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "weight_g > 0 AND length_cm > 0 AND width_cm > 0 AND height_cm > 0",
            name="ck_sku_measures",
        ),
        sa.CheckConstraint("purchase_price IS NULL OR purchase_price >= 0", name="ck_sku_purchase_price"),
        sa.CheckConstraint(
            "(purchase_price IS NULL AND currency IS NULL) OR (purchase_price IS NOT NULL AND currency IS NOT NULL)",
            name="ck_sku_purchase_currency",
        ),
        sa.ForeignKeyConstraint(["spu_id"], ["spu.id"], name="fk_sku_spu_id_spu"),
        sa.PrimaryKeyConstraint("id", name="pk_sku"),
    )
    op.create_index("ix_sku_tenant_id", "sku", ["tenant_id"])
    op.create_index("ix_sku_created_at", "sku", ["created_at"])
    op.create_index("ix_sku_deleted_at", "sku", ["deleted_at"])
    op.create_index("ix_sku_tenant_id_spu_id", "sku", ["tenant_id", "spu_id"])
    op.create_index(
        "uq_sku_tenant_id_sku_code_active",
        "sku",
        ["tenant_id", "sku_code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _tenant_policy("sku")
    _grant_master("sku")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS sku_tenant_isolation ON sku")
    op.execute("DROP POLICY IF EXISTS spu_tenant_isolation ON spu")
    op.drop_index("uq_sku_tenant_id_sku_code_active", table_name="sku")
    op.drop_table("sku")
    op.drop_table("spu")
