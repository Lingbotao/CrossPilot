"""商品图片与 Listing 差异清单 —— M3-04

``product_image`` 是主数据，可软删除。``listing_diff`` 是待确认流水，不软删除。
应用角色没有 DELETE。RLS 手写。

Revision ID: 0013_catalog_media
Revises: 0012_listing_batch
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_catalog_media"
down_revision: str | None = "0012_listing_batch"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"
_IMAGE_TYPE_SQL = "image_type IN ('MAIN', 'GALLERY', 'APLUS')"
_DIFF_FIELD_SQL = "field_name IN ('price', 'currency')"
_DIFF_STATUS_SQL = "status IN ('PENDING', 'ACCEPTED', 'DISMISSED')"


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


def upgrade() -> None:
    op.create_table(
        "product_image",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("spu_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=True),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("image_type", sa.String(length=16), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("width_px", sa.Integer(), nullable=False),
        sa.Column("height_px", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("white_background", sa.Boolean(), nullable=False),
        sa.Column(
            "platform_compliance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_IMAGE_TYPE_SQL, name="ck_product_image_image_type"),
        sa.CheckConstraint(
            "width_px > 0 AND height_px > 0 AND byte_size > 0 AND sort >= 0",
            name="ck_product_image_measures",
        ),
        sa.ForeignKeyConstraint(["spu_id"], ["spu.id"], name="fk_product_image_spu_id_spu"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_product_image_sku_id_sku"),
        sa.PrimaryKeyConstraint("id", name="pk_product_image"),
    )
    op.create_index("ix_product_image_tenant_id", "product_image", ["tenant_id"])
    op.create_index("ix_product_image_created_at", "product_image", ["created_at"])
    op.create_index("ix_product_image_deleted_at", "product_image", ["deleted_at"])
    op.create_index("ix_product_image_tenant_id_spu_id", "product_image", ["tenant_id", "spu_id"])
    _tenant_policy("product_image")
    _grant("product_image")

    op.create_table(
        "listing_diff",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("field_name", sa.String(length=16), nullable=False),
        sa.Column("local_value", sa.String(length=64), nullable=True),
        sa.Column("remote_value", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(_DIFF_FIELD_SQL, name="ck_listing_diff_field_name"),
        sa.CheckConstraint(_DIFF_STATUS_SQL, name="ck_listing_diff_status"),
        sa.ForeignKeyConstraint(["listing_id"], ["listing.id"], name="fk_listing_diff_listing_id_listing"),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_listing_diff_shop_id_shop"),
        sa.PrimaryKeyConstraint("id", name="pk_listing_diff"),
    )
    op.create_index("ix_listing_diff_tenant_id", "listing_diff", ["tenant_id"])
    op.create_index("ix_listing_diff_created_at", "listing_diff", ["created_at"])
    op.create_index("ix_listing_diff_tenant_id_status", "listing_diff", ["tenant_id", "status"])
    op.create_index(
        "uq_listing_diff_pending",
        "listing_diff",
        ["tenant_id", "listing_id", "field_name"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    _tenant_policy("listing_diff")
    _grant("listing_diff")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS listing_diff_tenant_isolation ON listing_diff")
    op.execute("DROP POLICY IF EXISTS product_image_tenant_isolation ON product_image")
    op.drop_index("uq_listing_diff_pending", table_name="listing_diff")
    op.drop_table("listing_diff")
    op.drop_index("ix_product_image_tenant_id_spu_id", table_name="product_image")
    op.drop_table("product_image")
