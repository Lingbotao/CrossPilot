"""采购域 —— M5-01 / M5-02

供应商和 SKU 供应关系可软删除。采购单、收货、头程、分摊和成本池是账本，应用角色不能删除。

Revision ID: 0023_purchase_domain
Revises: 0022_tax_registration_settlement
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023_purchase_domain"
down_revision: str | None = "0022_tax_registration_settlement"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{item}'" for item in values)


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


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
    ]


def _touch_indexes(table: str, *, soft: bool) -> None:
    op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
    op.create_index(f"ix_{table}_created_at", table, ["created_at"])
    if soft:
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"])


def upgrade() -> None:
    from app.engines.landed_cost import CHANNELS
    from app.engines.purchase import ALLOC_METHODS, PO_STATUSES, SETTLEMENT_TYPES, SHIPMENT_STATUSES
    from app.models.locale import CONTENT_MARKETS

    statuses = _quoted(PO_STATUSES)
    settlements = _quoted(SETTLEMENT_TYPES)
    channels = _quoted(tuple(item for item in CHANNELS if item != "*"))
    methods = _quoted(ALLOC_METHODS)
    shipment_statuses = _quoted(SHIPMENT_STATUSES)
    markets = _quoted(CONTENT_MARKETS)

    op.create_table(
        "supplier",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("contact", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("settlement_type", sa.String(length=16), nullable=False),
        sa.Column("credit_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        *_audit_columns(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"settlement_type IN ({settlements})", name="ck_supplier_settlement_type"),
        sa.CheckConstraint("char_length(btrim(name)) > 0", name="ck_supplier_name"),
        sa.CheckConstraint("credit_days >= 0", name="ck_supplier_credit_days"),
        sa.CheckConstraint("rating IS NULL OR (rating >= 1 AND rating <= 5)", name="ck_supplier_rating"),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="ck_supplier_idempotency_key",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_supplier"),
    )
    _touch_indexes("supplier", soft=True)
    op.create_index(
        "uq_supplier_name_active",
        "supplier",
        ["tenant_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_supplier_idempotency",
        "supplier",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
    )
    _tenant_policy("supplier")
    _grant_ledger("supplier")

    op.create_table(
        "sku_supplier",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_audit_columns(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"], name="fk_sku_supplier_supplier_id_supplier"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_sku_supplier_sku_id_sku"),
        sa.PrimaryKeyConstraint("id", name="pk_sku_supplier"),
    )
    _touch_indexes("sku_supplier", soft=True)
    op.create_index(
        "uq_sku_supplier_pair_active",
        "sku_supplier",
        ["tenant_id", "supplier_id", "sku_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_sku_supplier_default",
        "sku_supplier",
        ["tenant_id", "sku_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND deleted_at IS NULL"),
    )
    op.create_index("ix_sku_supplier_tenant_id_supplier_id", "sku_supplier", ["tenant_id", "supplier_id"])
    _tenant_policy("sku_supplier")
    _grant_ledger("sku_supplier")

    op.create_table(
        "purchase_order",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("expected_on", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(f"status IN ({statuses})", name="ck_purchase_order_status"),
        sa.CheckConstraint("total_amount >= 0", name="ck_purchase_order_total_amount"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_purchase_order_currency"),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="ck_purchase_order_idempotency_key",
        ),
        sa.ForeignKeyConstraint(["supplier_id"], ["supplier.id"], name="fk_purchase_order_supplier_id_supplier"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouse.id"], name="fk_purchase_order_warehouse_id_warehouse"),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_order"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_purchase_order_idempotency"),
    )
    _touch_indexes("purchase_order", soft=False)
    op.create_index("ix_purchase_order_tenant_id_status", "purchase_order", ["tenant_id", "status"])
    _tenant_policy("purchase_order")
    _grant_ledger("purchase_order")

    op.create_table(
        "purchase_order_item",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("purchase_order_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("received_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("short_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unit_price", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("tax_included", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("expected_on", sa.Date(), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(
            "quantity > 0 AND received_qty >= 0 AND short_qty >= 0",
            name="ck_purchase_order_item_quantity",
        ),
        sa.CheckConstraint("unit_price >= 0", name="ck_purchase_order_item_unit_price"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_purchase_order_item_currency"),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_order.id"],
            name="fk_purchase_order_item_purchase_order_id_purchase_order",
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_purchase_order_item_sku_id_sku"),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_order_item"),
        sa.UniqueConstraint(
            "tenant_id",
            "purchase_order_id",
            "sku_id",
            name="uq_purchase_order_item_sku",
        ),
    )
    _touch_indexes("purchase_order_item", soft=False)
    op.create_index(
        "ix_purchase_order_item_tenant_id_purchase_order_id",
        "purchase_order_item",
        ["tenant_id", "purchase_order_id"],
    )
    _tenant_policy("purchase_order_item")
    _grant_ledger("purchase_order_item")

    op.create_table(
        "purchase_receipt",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("purchase_order_id", sa.BigInteger(), nullable=False),
        sa.Column("disposition", sa.String(length=16), nullable=False),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint("disposition IN ('RECEIVE', 'SHORT')", name="ck_purchase_receipt_disposition"),
        sa.CheckConstraint("jsonb_typeof(lines) = 'array'", name="ck_purchase_receipt_lines"),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="ck_purchase_receipt_idempotency_key",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_order.id"],
            name="fk_purchase_receipt_purchase_order_id_purchase_order",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_receipt"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_purchase_receipt_idempotency"),
    )
    _touch_indexes("purchase_receipt", soft=False)
    op.create_index(
        "ix_purchase_receipt_tenant_id_purchase_order_id",
        "purchase_receipt",
        ["tenant_id", "purchase_order_id"],
    )
    _tenant_policy("purchase_receipt")
    _grant_ledger("purchase_receipt")

    op.create_table(
        "first_mile_shipment",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("purchase_order_id", sa.BigInteger(), nullable=True),
        sa.Column("from_warehouse_id", sa.BigInteger(), nullable=True),
        sa.Column("to_warehouse_id", sa.BigInteger(), nullable=True),
        sa.Column("forwarder", sa.String(length=128), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("container_no", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("destination_market", sa.String(length=2), nullable=False),
        sa.Column("cost_total", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("alloc_method", sa.String(length=16), nullable=False),
        sa.Column("storage_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(f"channel IN ({channels})", name="ck_first_mile_shipment_channel"),
        sa.CheckConstraint(f"destination_market IN ({markets})", name="ck_first_mile_shipment_destination_market"),
        sa.CheckConstraint(f"alloc_method IN ({methods})", name="ck_first_mile_shipment_alloc_method"),
        sa.CheckConstraint(f"status IN ({shipment_statuses})", name="ck_first_mile_shipment_status"),
        sa.CheckConstraint("cost_total > 0", name="ck_first_mile_shipment_cost_total"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_first_mile_shipment_currency"),
        sa.CheckConstraint("char_length(btrim(forwarder)) > 0", name="ck_first_mile_shipment_forwarder"),
        sa.CheckConstraint("storage_days >= 0", name="ck_first_mile_shipment_storage_days"),
        sa.CheckConstraint("jsonb_typeof(lines) = 'array'", name="ck_first_mile_shipment_lines"),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="ck_first_mile_shipment_idempotency_key",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_order.id"],
            name="fk_first_mile_shipment_purchase_order_id_purchase_order",
        ),
        sa.ForeignKeyConstraint(
            ["from_warehouse_id"],
            ["warehouse.id"],
            name="fk_first_mile_shipment_from_warehouse_id_warehouse",
        ),
        sa.ForeignKeyConstraint(
            ["to_warehouse_id"],
            ["warehouse.id"],
            name="fk_first_mile_shipment_to_warehouse_id_warehouse",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_first_mile_shipment"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_first_mile_shipment_idempotency"),
    )
    _touch_indexes("first_mile_shipment", soft=False)
    op.create_index("ix_first_mile_shipment_tenant_id_status", "first_mile_shipment", ["tenant_id", "status"])
    _tenant_policy("first_mile_shipment")
    _grant_ledger("first_mile_shipment")

    op.create_table(
        "first_mile_cost_allocation",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("allocated_cost", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(f"method IN ({methods})", name="ck_first_mile_cost_allocation_method"),
        sa.CheckConstraint("quantity > 0 AND allocated_cost >= 0", name="ck_first_mile_cost_allocation_quantity"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_first_mile_cost_allocation_currency"),
        sa.CheckConstraint(
            "char_length(btrim(formula)) > 0 AND char_length(btrim(source)) > 0",
            name="ck_first_mile_cost_allocation_explain",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["first_mile_shipment.id"],
            name="fk_first_mile_cost_allocation_shipment_id_first_mile_shipment",
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_first_mile_cost_allocation_sku_id_sku"),
        sa.PrimaryKeyConstraint("id", name="pk_first_mile_cost_allocation"),
        sa.UniqueConstraint("tenant_id", "shipment_id", "sku_id", name="uq_first_mile_cost_allocation_sku"),
    )
    _touch_indexes("first_mile_cost_allocation", soft=False)
    op.create_index(
        "ix_first_mile_cost_allocation_tenant_id_shipment_id",
        "first_mile_cost_allocation",
        ["tenant_id", "shipment_id"],
    )
    _tenant_policy("first_mile_cost_allocation")
    _grant_ledger("first_mile_cost_allocation")

    op.create_table(
        "sku_cost_pool",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("purchase_order_id", sa.BigInteger(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("purchase_unit", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("first_mile_unit", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("duty_unit", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("import_tax_unit", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("brokerage_unit", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("storage_unit", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("fx_reserve_unit", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("landed_unit", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint("quantity > 0", name="ck_sku_cost_pool_quantity"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_sku_cost_pool_currency"),
        sa.CheckConstraint(
            "purchase_unit >= 0 AND first_mile_unit >= 0 AND duty_unit >= 0 AND import_tax_unit >= 0"
            " AND brokerage_unit >= 0 AND storage_unit >= 0 AND fx_reserve_unit >= 0 AND landed_unit >= 0",
            name="ck_sku_cost_pool_units",
        ),
        sa.CheckConstraint("jsonb_typeof(lines) = 'array'", name="ck_sku_cost_pool_lines"),
        sa.CheckConstraint("char_length(btrim(source)) > 0", name="ck_sku_cost_pool_source"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_sku_cost_pool_sku_id_sku"),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["first_mile_shipment.id"],
            name="fk_sku_cost_pool_shipment_id_first_mile_shipment",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_order.id"],
            name="fk_sku_cost_pool_purchase_order_id_purchase_order",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sku_cost_pool"),
    )
    _touch_indexes("sku_cost_pool", soft=False)
    op.create_index("ix_sku_cost_pool_tenant_id_sku_id", "sku_cost_pool", ["tenant_id", "sku_id"])
    _tenant_policy("sku_cost_pool")
    _grant_ledger("sku_cost_pool")


def downgrade() -> None:
    op.drop_table("sku_cost_pool")
    op.drop_table("first_mile_cost_allocation")
    op.drop_table("first_mile_shipment")
    op.drop_table("purchase_receipt")
    op.drop_table("purchase_order_item")
    op.drop_table("purchase_order")
    op.drop_table("sku_supplier")
    op.drop_table("supplier")
