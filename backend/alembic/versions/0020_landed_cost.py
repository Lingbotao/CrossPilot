"""落地成本费用规则与计算留痕 —— M4-05

两张表都按租户隔离，应用角色不能删除。不写入任何费率种子。

Revision ID: 0020_landed_cost
Revises: 0019_spu_category_code
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020_landed_cost"
down_revision: str | None = "0019_spu_category_code"
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
    from app.engines.landed_cost import CHANNELS, CHARGES, FEE_CODES
    from app.models.landed_cost import CALC_KINDS, FEE_STATUSES
    from app.models.locale import CONTENT_MARKETS

    markets = _quoted(CONTENT_MARKETS)
    channels = _quoted(CHANNELS)
    fees = _quoted(FEE_CODES)
    charges = _quoted(CHARGES)
    statuses = _quoted(FEE_STATUSES)
    kinds = _quoted(CALC_KINDS)

    op.create_table(
        "landed_cost_fee",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("fee_code", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False, server_default="*"),
        sa.Column("charge", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("volumetric_divisor", sa.Integer(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("verified_by", sa.BigInteger(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(f"market IN ({markets})", name="ck_landed_cost_fee_market"),
        sa.CheckConstraint(f"channel IN ({channels})", name="ck_landed_cost_fee_channel"),
        sa.CheckConstraint(f"fee_code IN ({fees})", name="ck_landed_cost_fee_fee_code"),
        sa.CheckConstraint(f"charge IN ({charges})", name="ck_landed_cost_fee_charge"),
        sa.CheckConstraint(f"status IN ({statuses})", name="ck_landed_cost_fee_status"),
        sa.CheckConstraint("char_length(btrim(label)) > 0", name="ck_landed_cost_fee_label"),
        sa.CheckConstraint("amount >= 0", name="ck_landed_cost_fee_amount"),
        sa.CheckConstraint("charge <> 'RATE' OR amount <= 10", name="ck_landed_cost_fee_rate_cap"),
        sa.CheckConstraint(
            "(charge = 'RATE' AND currency IS NULL) OR (charge <> 'RATE' AND currency IS NOT NULL)",
            name="ck_landed_cost_fee_currency",
        ),
        sa.CheckConstraint(
            "volumetric_divisor IS NULL OR volumetric_divisor >= 1",
            name="ck_landed_cost_fee_divisor",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_landed_cost_fee_effective",
        ),
        sa.CheckConstraint("char_length(btrim(source)) > 0", name="ck_landed_cost_fee_source"),
        sa.CheckConstraint("version >= 1", name="ck_landed_cost_fee_version"),
        sa.PrimaryKeyConstraint("id", name="pk_landed_cost_fee"),
        sa.UniqueConstraint(
            "tenant_id",
            "market",
            "channel",
            "fee_code",
            "label",
            "version",
            name="uq_landed_cost_fee_version",
        ),
    )
    op.create_index("ix_landed_cost_fee_tenant_id", "landed_cost_fee", ["tenant_id"])
    op.create_index("ix_landed_cost_fee_created_at", "landed_cost_fee", ["created_at"])
    op.create_index("ix_landed_cost_fee_tenant_id_market", "landed_cost_fee", ["tenant_id", "market"])
    _tenant_policy("landed_cost_fee")
    _grant_ledger("landed_cost_fee")

    op.create_table(
        "landed_cost_calc",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=True),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(f"market IN ({markets})", name="ck_landed_cost_calc_market"),
        sa.CheckConstraint(f"kind IN ({kinds})", name="ck_landed_cost_calc_kind"),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="ck_landed_cost_calc_idempotency_key",
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_landed_cost_calc_sku_id_sku"),
        sa.PrimaryKeyConstraint("id", name="pk_landed_cost_calc"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_landed_cost_calc_idempotency"),
    )
    op.create_index("ix_landed_cost_calc_tenant_id", "landed_cost_calc", ["tenant_id"])
    op.create_index("ix_landed_cost_calc_created_at", "landed_cost_calc", ["created_at"])
    op.create_index("ix_landed_cost_calc_tenant_id_created_at", "landed_cost_calc", ["tenant_id", "created_at"])
    _tenant_policy("landed_cost_calc")
    _grant_ledger("landed_cost_calc")


def downgrade() -> None:
    op.drop_table("landed_cost_calc")
    op.drop_table("landed_cost_fee")
