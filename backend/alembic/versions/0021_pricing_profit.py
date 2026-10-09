"""成本项开关、汇率与 SKU 日利润 —— M4-06 / M4-07

日利润按 stat_date 月分区。不写入汇率或费率种子。应用角色不能删除。

Revision ID: 0021_pricing_profit
Revises: 0020_landed_cost
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021_pricing_profit"
down_revision: str | None = "0020_landed_cost"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"
_PARTITION_MONTHS_BACK = 1
_PARTITION_MONTHS_FORWARD = 12


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{item}'" for item in values)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


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


def _grant_ledger(table: str, *, partitions: bool = False) -> None:
    extra = ""
    if partitions:
        extra = f"""
                FOR part IN
                    SELECT inhrelid FROM pg_inherits WHERE inhparent = '{table}'::regclass
                LOOP
                    EXECUTE format(
                        'GRANT SELECT, INSERT, UPDATE ON TABLE %s TO {_APP_ROLE}',
                        part
                    );
                    EXECUTE format('REVOKE DELETE ON TABLE %s FROM {_APP_ROLE}', part);
                END LOOP;
        """
    op.execute(
        f"""
        DO $$
        DECLARE
            part regclass;
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE ON {table} TO {_APP_ROLE};
                REVOKE DELETE ON {table} FROM {_APP_ROLE};
                {extra}
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


def _create_profit_partitions() -> None:
    today = date.today().replace(day=1)
    start = _add_months(today, -_PARTITION_MONTHS_BACK)
    for i in range(_PARTITION_MONTHS_BACK + _PARTITION_MONTHS_FORWARD + 1):
        lower = _add_months(start, i)
        upper = _add_months(start, i + 1)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS sku_profit_daily_{lower:%Y_%m} "
            f"PARTITION OF sku_profit_daily FOR VALUES FROM ('{lower.isoformat()}') TO ('{upper.isoformat()}')"
        )


def upgrade() -> None:
    from app.engines.landed_cost import CHANNELS, LINE_CODES
    from app.models.finance import RATE_BASES
    from app.models.landed_cost import CALC_KINDS
    from app.models.locale import CONTENT_MARKETS

    markets = _quoted(CONTENT_MARKETS)
    channels = _quoted(CHANNELS)
    lines = _quoted(LINE_CODES)
    kinds = _quoted(CALC_KINDS)
    bases = _quoted(RATE_BASES)

    op.drop_constraint("ck_landed_cost_calc_kind", "landed_cost_calc", type_="check")
    op.create_check_constraint(
        "ck_landed_cost_calc_kind",
        "landed_cost_calc",
        f"kind IN ({kinds})",
    )

    op.create_table(
        "landed_cost_line_toggle",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("line_code", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(f"market IN ({markets})", name="ck_landed_cost_line_toggle_market"),
        sa.CheckConstraint(f"channel IN ({channels})", name="ck_landed_cost_line_toggle_channel"),
        sa.CheckConstraint(f"line_code IN ({lines})", name="ck_landed_cost_line_toggle_line_code"),
        sa.PrimaryKeyConstraint("id", name="pk_landed_cost_line_toggle"),
        sa.UniqueConstraint(
            "tenant_id",
            "market",
            "channel",
            "line_code",
            name="uq_landed_cost_line_toggle_key",
        ),
    )
    op.create_index("ix_landed_cost_line_toggle_tenant_id", "landed_cost_line_toggle", ["tenant_id"])
    op.create_index("ix_landed_cost_line_toggle_created_at", "landed_cost_line_toggle", ["created_at"])
    op.create_index(
        "ix_landed_cost_line_toggle_tenant_id_market",
        "landed_cost_line_toggle",
        ["tenant_id", "market"],
    )
    _tenant_policy("landed_cost_line_toggle")
    _grant_ledger("landed_cost_line_toggle")

    op.create_table(
        "exchange_rate",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("base_currency", sa.CHAR(length=3), nullable=False),
        sa.Column("quote_currency", sa.CHAR(length=3), nullable=False),
        sa.Column("rate", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("basis", sa.String(length=16), nullable=False),
        sa.Column("effective_on", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(f"basis IN ({bases})", name="ck_exchange_rate_basis"),
        sa.CheckConstraint("rate > 0", name="ck_exchange_rate_rate"),
        sa.CheckConstraint("base_currency <> quote_currency", name="ck_exchange_rate_pair"),
        sa.CheckConstraint("char_length(btrim(source)) > 0", name="ck_exchange_rate_source"),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="ck_exchange_rate_idempotency_key",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_exchange_rate"),
        sa.UniqueConstraint(
            "tenant_id",
            "base_currency",
            "quote_currency",
            "basis",
            "effective_on",
            name="uq_exchange_rate_key",
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_exchange_rate_idempotency"),
    )
    op.create_index("ix_exchange_rate_tenant_id", "exchange_rate", ["tenant_id"])
    op.create_index("ix_exchange_rate_created_at", "exchange_rate", ["created_at"])
    op.create_index(
        "ix_exchange_rate_tenant_id_pair",
        "exchange_rate",
        ["tenant_id", "base_currency", "quote_currency", "basis", "effective_on"],
    )
    _tenant_policy("exchange_rate")
    _grant_ledger("exchange_rate")

    op.create_table(
        "sku_profit_daily",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("book_currency", sa.CHAR(length=3), nullable=False),
        sa.Column("revenue", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("cost_total", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("net_profit", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("net_margin", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("book_revenue", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("book_cost_total", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("book_net_profit", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("fx_gain", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("lines", postgresql.JSONB(), nullable=False),
        sa.Column("fx_formula", sa.Text(), nullable=False, server_default=""),
        sa.Column("fx_source", sa.Text(), nullable=False, server_default=""),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_sku_profit_daily_quantity"),
        sa.CheckConstraint("revenue >= 0", name="ck_sku_profit_daily_revenue"),
        sa.PrimaryKeyConstraint("id", "stat_date", name="pk_sku_profit_daily"),
        sa.UniqueConstraint(
            "tenant_id",
            "sku_id",
            "shop_id",
            "stat_date",
            "currency",
            name="uq_sku_profit_daily_key",
        ),
        postgresql_partition_by="RANGE (stat_date)",
    )
    op.create_index("ix_sku_profit_daily_tenant_id", "sku_profit_daily", ["tenant_id"])
    op.create_index("ix_sku_profit_daily_tenant_id_stat_date", "sku_profit_daily", ["tenant_id", "stat_date"])
    _create_profit_partitions()
    _tenant_policy("sku_profit_daily")
    _grant_ledger("sku_profit_daily", partitions=True)


def downgrade() -> None:
    op.drop_table("sku_profit_daily")
    op.drop_table("exchange_rate")
    op.drop_table("landed_cost_line_toggle")
    op.drop_constraint("ck_landed_cost_calc_kind", "landed_cost_calc", type_="check")
    op.create_check_constraint(
        "ck_landed_cost_calc_kind",
        "landed_cost_calc",
        "kind IN ('CALC', 'COMPARE')",
    )
