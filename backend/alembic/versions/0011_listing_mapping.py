"""Listing 映射与类目模板 —— M3-02

主数据可软删除。应用角色没有 DELETE。
平台 SKU 唯一索引只覆盖未删除且已填写平台 SKU 的行。RLS 手写。

Revision ID: 0011_listing_mapping
Revises: 0010_product_master
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_listing_mapping"
down_revision: str | None = "0010_product_master"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"
_STATUS_SQL = "status IN ('DRAFT', 'LINKED', 'UNLISTED')"


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
        "category_mapping",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("site_code", sa.String(length=8), nullable=False),
        sa.Column("platform_category_id", sa.String(length=128), nullable=False),
        sa.Column("local_category_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("attrs_template", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_category_mapping"),
    )
    op.create_index("ix_category_mapping_tenant_id", "category_mapping", ["tenant_id"])
    op.create_index("ix_category_mapping_created_at", "category_mapping", ["created_at"])
    op.create_index("ix_category_mapping_deleted_at", "category_mapping", ["deleted_at"])
    op.create_index(
        "ix_category_mapping_tenant_id_platform_site",
        "category_mapping",
        ["tenant_id", "platform_code", "site_code"],
    )
    op.create_index(
        "uq_category_mapping_tenant_platform_site_code_active",
        "category_mapping",
        ["tenant_id", "platform_code", "site_code", "local_category_code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _tenant_policy("category_mapping")
    _grant_master("category_mapping")

    op.create_table(
        "listing",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("category_mapping_id", sa.BigInteger(), nullable=True),
        sa.Column("platform_product_id", sa.String(length=128), nullable=True),
        sa.Column("platform_sku_id", sa.String(length=128), nullable=True),
        sa.Column("price", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("attr_values", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="DRAFT"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_STATUS_SQL, name="ck_listing_status"),
        sa.CheckConstraint("price IS NULL OR price >= 0", name="ck_listing_price"),
        sa.CheckConstraint(
            "(price IS NULL AND currency IS NULL) OR (price IS NOT NULL AND currency IS NOT NULL)",
            name="ck_listing_price_currency",
        ),
        sa.CheckConstraint(
            "status <> 'LINKED' OR (platform_product_id IS NOT NULL AND platform_sku_id IS NOT NULL)",
            name="ck_listing_linked_ids",
        ),
        sa.CheckConstraint(
            "status <> 'DRAFT' OR platform_product_id IS NULL OR platform_sku_id IS NULL",
            name="ck_listing_draft_ids",
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_listing_sku_id_sku"),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_listing_shop_id_shop"),
        sa.ForeignKeyConstraint(
            ["category_mapping_id"],
            ["category_mapping.id"],
            name="fk_listing_category_mapping_id_category_mapping",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_listing"),
    )
    op.create_index("ix_listing_tenant_id", "listing", ["tenant_id"])
    op.create_index("ix_listing_created_at", "listing", ["created_at"])
    op.create_index("ix_listing_deleted_at", "listing", ["deleted_at"])
    op.create_index("ix_listing_tenant_id_sku_id", "listing", ["tenant_id", "sku_id"])
    op.create_index("ix_listing_tenant_id_shop_id", "listing", ["tenant_id", "shop_id"])
    op.create_index("ix_listing_tenant_id_platform_product_id", "listing", ["tenant_id", "platform_product_id"])
    op.create_index(
        "uq_listing_tenant_shop_platform_sku_active",
        "listing",
        ["tenant_id", "shop_id", "platform_sku_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND platform_sku_id IS NOT NULL"),
    )
    _tenant_policy("listing")
    _grant_master("listing")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS listing_tenant_isolation ON listing")
    op.execute("DROP POLICY IF EXISTS category_mapping_tenant_isolation ON category_mapping")
    op.drop_index("uq_listing_tenant_shop_platform_sku_active", table_name="listing")
    op.drop_table("listing")
    op.drop_index("uq_category_mapping_tenant_platform_site_code_active", table_name="category_mapping")
    op.drop_table("category_mapping")
