"""批量刊登与批量改价 —— M3-03

操作流水，不软删除。应用角色没有 DELETE。RLS 手写。

Revision ID: 0012_listing_batch
Revises: 0011_listing_mapping
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0012_listing_batch"
down_revision: str | None = "0011_listing_mapping"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"
_KIND_SQL = "kind IN ('PUBLISH', 'PRICE')"
_STATUS_SQL = "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')"
_ITEM_STATUS_SQL = "status IN ('PENDING', 'SUCCEEDED', 'FAILED', 'SKIPPED')"
_PRICE_BEFORE = (
    "(price_before IS NULL AND currency_before IS NULL) "
    "OR (price_before IS NOT NULL AND currency_before IS NOT NULL)"
)
_PRICE_AFTER = (
    "(price_after IS NULL AND currency_after IS NULL) OR (price_after IS NOT NULL AND currency_after IS NOT NULL)"
)


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


def _grant_ledger(table: str) -> None:
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
        "listing_batch",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(_KIND_SQL, name="ck_listing_batch_kind"),
        sa.CheckConstraint(_STATUS_SQL, name="ck_listing_batch_status"),
        sa.CheckConstraint(
            "total >= 0 AND succeeded >= 0 AND failed >= 0 AND skipped >= 0",
            name="ck_listing_batch_counts",
        ),
        sa.CheckConstraint("succeeded + failed + skipped <= total", name="ck_listing_batch_count_sum"),
        sa.PrimaryKeyConstraint("id", name="pk_listing_batch"),
    )
    op.create_index("ix_listing_batch_tenant_id", "listing_batch", ["tenant_id"])
    op.create_index("ix_listing_batch_created_at", "listing_batch", ["created_at"])
    op.create_index("ix_listing_batch_tenant_id_created_at", "listing_batch", ["tenant_id", "created_at"])
    _tenant_policy("listing_batch")
    _grant_ledger("listing_batch")

    op.create_table(
        "listing_batch_item",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("price_before", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("currency_before", sa.String(length=3), nullable=True),
        sa.Column("price_after", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("currency_after", sa.String(length=3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(_ITEM_STATUS_SQL, name="ck_listing_batch_item_status"),
        sa.CheckConstraint("price_before IS NULL OR price_before >= 0", name="ck_listing_batch_item_price_before_nonneg"),
        sa.CheckConstraint("price_after IS NULL OR price_after >= 0", name="ck_listing_batch_item_price_after_nonneg"),
        sa.CheckConstraint(_PRICE_BEFORE, name="ck_listing_batch_item_price_before"),
        sa.CheckConstraint(_PRICE_AFTER, name="ck_listing_batch_item_price_after"),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["listing_batch.id"],
            name="fk_listing_batch_item_batch_id_listing_batch",
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_listing_batch_item_sku_id_sku"),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_listing_batch_item_shop_id_shop"),
        sa.ForeignKeyConstraint(["listing_id"], ["listing.id"], name="fk_listing_batch_item_listing_id_listing"),
        sa.PrimaryKeyConstraint("id", name="pk_listing_batch_item"),
    )
    op.create_index("ix_listing_batch_item_tenant_id", "listing_batch_item", ["tenant_id"])
    op.create_index("ix_listing_batch_item_created_at", "listing_batch_item", ["created_at"])
    op.create_index(
        "ix_listing_batch_item_tenant_id_batch_id",
        "listing_batch_item",
        ["tenant_id", "batch_id"],
    )
    _tenant_policy("listing_batch_item")
    _grant_ledger("listing_batch_item")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS listing_batch_item_tenant_isolation ON listing_batch_item")
    op.execute("DROP POLICY IF EXISTS listing_batch_tenant_isolation ON listing_batch")
    op.drop_index("ix_listing_batch_item_tenant_id_batch_id", table_name="listing_batch_item")
    op.drop_table("listing_batch_item")
    op.drop_index("ix_listing_batch_tenant_id_created_at", table_name="listing_batch")
    op.drop_table("listing_batch")
