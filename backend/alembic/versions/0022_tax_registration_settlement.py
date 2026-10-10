"""税务注册与平台结算单 —— M4-08 / M4-10

税务注册可软删除。结算单和明细是账本，应用角色不能删除。不写种子数据。

Revision ID: 0022_tax_registration_settlement
Revises: 0021_pricing_profit
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0022_tax_registration_settlement"
down_revision: str | None = "0021_pricing_profit"
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


def upgrade() -> None:
    from app.models.compliance import FILING_CYCLES, TAX_TYPES
    from app.models.finance import MATCH_STATUSES
    from app.models.locale import CONTENT_MARKETS

    markets = _quoted(CONTENT_MARKETS)
    tax_types = _quoted(TAX_TYPES)
    cycles = _quoted(FILING_CYCLES)
    statuses = _quoted(MATCH_STATUSES)

    op.create_table(
        "tax_registration",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("tax_type", sa.String(length=16), nullable=False),
        sa.Column("tax_no", sa.String(length=64), nullable=False),
        sa.Column("entity", sa.String(length=256), nullable=False),
        sa.Column("agent", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("filing_cycle", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        *_audit_columns(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"country IN ({markets})", name="ck_tax_registration_country"),
        sa.CheckConstraint(f"tax_type IN ({tax_types})", name="ck_tax_registration_tax_type"),
        sa.CheckConstraint(f"filing_cycle IN ({cycles})", name="ck_tax_registration_filing_cycle"),
        sa.CheckConstraint("char_length(btrim(tax_no)) > 0", name="ck_tax_registration_tax_no"),
        sa.CheckConstraint("char_length(btrim(entity)) > 0", name="ck_tax_registration_entity"),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="ck_tax_registration_idempotency_key",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tax_registration"),
    )
    op.create_index("ix_tax_registration_tenant_id", "tax_registration", ["tenant_id"])
    op.create_index("ix_tax_registration_created_at", "tax_registration", ["created_at"])
    op.create_index("ix_tax_registration_deleted_at", "tax_registration", ["deleted_at"])
    op.create_index("ix_tax_registration_tenant_id_country", "tax_registration", ["tenant_id", "country"])
    op.create_index(
        "uq_tax_registration_active",
        "tax_registration",
        ["tenant_id", "country", "tax_type", "tax_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_tax_registration_idempotency",
        "tax_registration",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
    )
    _tenant_policy("tax_registration")
    _grant_ledger("tax_registration")

    op.create_table(
        "settlement",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("platform_settlement_id", sa.String(length=128), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("matched_count", sa.Integer(), nullable=False),
        sa.Column("match_rate", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint("period_end >= period_start", name="ck_settlement_period"),
        sa.CheckConstraint(
            "line_count >= 0 AND matched_count >= 0 AND matched_count <= line_count",
            name="ck_settlement_counts",
        ),
        sa.CheckConstraint("match_rate >= 0 AND match_rate <= 1", name="ck_settlement_match_rate"),
        sa.CheckConstraint(
            "char_length(btrim(platform_settlement_id)) > 0",
            name="ck_settlement_platform_settlement_id",
        ),
        sa.CheckConstraint("char_length(btrim(source)) > 0", name="ck_settlement_source"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_settlement_currency"),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="ck_settlement_idempotency_key",
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_settlement_shop_id_shop"),
        sa.PrimaryKeyConstraint("id", name="pk_settlement"),
        sa.UniqueConstraint(
            "tenant_id",
            "shop_id",
            "platform_settlement_id",
            name="uq_settlement_platform_id",
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_settlement_idempotency"),
    )
    op.create_index("ix_settlement_tenant_id", "settlement", ["tenant_id"])
    op.create_index("ix_settlement_created_at", "settlement", ["created_at"])
    op.create_index("ix_settlement_tenant_id_shop_id", "settlement", ["tenant_id", "shop_id"])
    _tenant_policy("settlement")
    _grant_ledger("settlement")

    op.create_table(
        "settlement_item",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("platform_order_id", sa.String(length=128), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("fee_type", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("match_status", sa.String(length=16), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(f"match_status IN ({statuses})", name="ck_settlement_item_match_status"),
        sa.CheckConstraint(
            "char_length(btrim(platform_order_id)) > 0",
            name="ck_settlement_item_platform_order_id",
        ),
        sa.CheckConstraint("char_length(btrim(fee_type)) > 0", name="ck_settlement_item_fee_type"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_settlement_item_currency"),
        sa.CheckConstraint(
            "(match_status = 'MATCHED' AND order_id IS NOT NULL) OR (match_status = 'UNMATCHED' AND order_id IS NULL)",
            name="ck_settlement_item_match_order",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlement.id"],
            name="fk_settlement_item_settlement_id_settlement",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["sales_order.id"], name="fk_settlement_item_order_id_sales_order"),
        sa.PrimaryKeyConstraint("id", name="pk_settlement_item"),
    )
    op.create_index("ix_settlement_item_tenant_id", "settlement_item", ["tenant_id"])
    op.create_index("ix_settlement_item_created_at", "settlement_item", ["created_at"])
    op.create_index(
        "ix_settlement_item_tenant_id_settlement_id",
        "settlement_item",
        ["tenant_id", "settlement_id"],
    )
    _tenant_policy("settlement_item")
    _grant_ledger("settlement_item")


def downgrade() -> None:
    op.drop_table("settlement_item")
    op.drop_table("settlement")
    op.drop_table("tax_registration")
