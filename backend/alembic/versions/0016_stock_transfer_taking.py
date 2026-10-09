"""调拨单、盘点单，以及调拨流水类型 —— M3-08

单据不软删除。应用角色没有 DELETE。流水类型补上 TRANSFER_OUT / TRANSFER_IN。

Revision ID: 0016_stock_transfer_taking
Revises: 0015_inventory_ledger
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_stock_transfer_taking"
down_revision: str | None = "0015_inventory_ledger"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"
_FLOW_SQL = "'INBOUND', 'OUTBOUND', 'RESERVE', 'RELEASE', 'SHIP', 'RETURN_IN', 'ADJUST', 'TRANSFER_OUT', 'TRANSFER_IN'"
_FLOW_SQL_PREVIOUS = "'INBOUND', 'OUTBOUND', 'RESERVE', 'RELEASE', 'SHIP', 'RETURN_IN', 'ADJUST'"
_TRANSFER_SQL = "'DRAFT', 'IN_TRANSIT', 'RECEIVED', 'CANCELLED'"
_TAKING_SQL = "'DRAFT', 'POSTED', 'CANCELLED'"


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


def upgrade() -> None:
    op.drop_constraint("ck_inventory_flow_flow_type", "inventory_flow", type_="check")
    op.create_check_constraint(
        "ck_inventory_flow_flow_type",
        "inventory_flow",
        f"flow_type IN ({_FLOW_SQL})",
    )

    op.create_table(
        "stock_transfer",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("from_warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("to_warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(f"status IN ({_TRANSFER_SQL})", name="ck_stock_transfer_status"),
        sa.CheckConstraint("from_warehouse_id <> to_warehouse_id", name="ck_stock_transfer_warehouses"),
        sa.ForeignKeyConstraint(
            ["from_warehouse_id"],
            ["warehouse.id"],
            name="fk_stock_transfer_from_warehouse_id_warehouse",
        ),
        sa.ForeignKeyConstraint(
            ["to_warehouse_id"],
            ["warehouse.id"],
            name="fk_stock_transfer_to_warehouse_id_warehouse",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stock_transfer"),
    )
    op.create_index("ix_stock_transfer_tenant_id", "stock_transfer", ["tenant_id"])
    op.create_index("ix_stock_transfer_created_at", "stock_transfer", ["created_at"])
    op.create_index("ix_stock_transfer_tenant_id_status", "stock_transfer", ["tenant_id", "status"])
    _tenant_policy("stock_transfer")
    _grant("stock_transfer")

    op.create_table(
        "stock_transfer_line",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("transfer_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint("quantity > 0", name="ck_stock_transfer_line_quantity"),
        sa.ForeignKeyConstraint(
            ["transfer_id"],
            ["stock_transfer.id"],
            name="fk_stock_transfer_line_transfer_id_stock_transfer",
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_stock_transfer_line_sku_id_sku"),
        sa.PrimaryKeyConstraint("id", name="pk_stock_transfer_line"),
    )
    op.create_index("ix_stock_transfer_line_tenant_id", "stock_transfer_line", ["tenant_id"])
    op.create_index("ix_stock_transfer_line_created_at", "stock_transfer_line", ["created_at"])
    op.create_index(
        "ix_stock_transfer_line_tenant_id_transfer_id",
        "stock_transfer_line",
        ["tenant_id", "transfer_id"],
    )
    op.create_index(
        "uq_stock_transfer_line_tenant_transfer_sku",
        "stock_transfer_line",
        ["tenant_id", "transfer_id", "sku_id"],
        unique=True,
    )
    _tenant_policy("stock_transfer_line")
    _grant("stock_transfer_line")

    op.create_table(
        "stock_taking",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "diff_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        *_audit_columns(),
        sa.CheckConstraint(f"status IN ({_TAKING_SQL})", name="ck_stock_taking_status"),
        sa.CheckConstraint("jsonb_typeof(diff_summary) = 'object'", name="ck_stock_taking_diff_summary"),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouse.id"],
            name="fk_stock_taking_warehouse_id_warehouse",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stock_taking"),
    )
    op.create_index("ix_stock_taking_tenant_id", "stock_taking", ["tenant_id"])
    op.create_index("ix_stock_taking_created_at", "stock_taking", ["created_at"])
    op.create_index("ix_stock_taking_tenant_id_warehouse_id", "stock_taking", ["tenant_id", "warehouse_id"])
    _tenant_policy("stock_taking")
    _grant("stock_taking")

    op.create_table(
        "stock_taking_line",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("taking_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("book_qty", sa.Integer(), nullable=False),
        sa.Column("counted_qty", sa.Integer(), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(
            "book_qty >= 0 AND (counted_qty IS NULL OR counted_qty >= 0)",
            name="ck_stock_taking_line_quantity",
        ),
        sa.ForeignKeyConstraint(
            ["taking_id"],
            ["stock_taking.id"],
            name="fk_stock_taking_line_taking_id_stock_taking",
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_stock_taking_line_sku_id_sku"),
        sa.PrimaryKeyConstraint("id", name="pk_stock_taking_line"),
    )
    op.create_index("ix_stock_taking_line_tenant_id", "stock_taking_line", ["tenant_id"])
    op.create_index("ix_stock_taking_line_created_at", "stock_taking_line", ["created_at"])
    op.create_index("ix_stock_taking_line_tenant_id_taking_id", "stock_taking_line", ["tenant_id", "taking_id"])
    op.create_index(
        "uq_stock_taking_line_tenant_taking_sku",
        "stock_taking_line",
        ["tenant_id", "taking_id", "sku_id"],
        unique=True,
    )
    _tenant_policy("stock_taking_line")
    _grant("stock_taking_line")


def downgrade() -> None:
    op.drop_table("stock_taking_line")
    op.drop_table("stock_taking")
    op.drop_table("stock_transfer_line")
    op.drop_table("stock_transfer")
    op.drop_constraint("ck_inventory_flow_flow_type", "inventory_flow", type_="check")
    op.create_check_constraint(
        "ck_inventory_flow_flow_type",
        "inventory_flow",
        f"flow_type IN ({_FLOW_SQL_PREVIOUS})",
    )
