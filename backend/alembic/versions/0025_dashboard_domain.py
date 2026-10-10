"""经营看板汇总表 —— M5-04

店铺日汇总和库存健康快照按 stat_date 月分区。
汇总可以按区间替换，所以应用角色保留 DELETE。订单流水仍然不能删。

Revision ID: 0025_dashboard_domain
Revises: 0024_ads_domain
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025_dashboard_domain"
down_revision: str | None = "0024_ads_domain"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"
_PARTITION_MONTHS_BACK = 1
_PARTITION_MONTHS_FORWARD = 12


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


def _grant(table: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            part regclass;
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {_APP_ROLE};
                FOR part IN
                    SELECT inhrelid FROM pg_inherits WHERE inhparent = '{table}'::regclass
                LOOP
                    EXECUTE format(
                        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %s TO {_APP_ROLE}',
                        part
                    );
                END LOOP;
            END IF;
        END
        $$;
        """  # noqa: S608
    )


def _create_partitions(table: str) -> None:
    today = date.today().replace(day=1)
    start = _add_months(today, -_PARTITION_MONTHS_BACK)
    for i in range(_PARTITION_MONTHS_BACK + _PARTITION_MONTHS_FORWARD + 1):
        lower = _add_months(start, i)
        upper = _add_months(start, i + 1)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS {table}_{lower:%Y_%m} "
            f"PARTITION OF {table} FOR VALUES FROM ('{lower.isoformat()}') TO ('{upper.isoformat()}')"
        )


def _shop_daily() -> None:
    op.create_table(
        "dashboard_shop_daily",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("site_code", sa.String(length=8), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("book_currency", sa.String(length=3), nullable=False),
        sa.Column("order_count", sa.Integer(), nullable=False),
        sa.Column("gmv", sa.Numeric(20, 6), nullable=False),
        sa.Column("book_gmv", sa.Numeric(20, 6), nullable=True),
        sa.Column("net_profit", sa.Numeric(20, 6), nullable=True),
        sa.Column("book_net_profit", sa.Numeric(20, 6), nullable=True),
        sa.Column("profit_complete", sa.Boolean(), nullable=False),
        sa.Column("on_time", sa.Integer(), nullable=False),
        sa.Column("late", sa.Integer(), nullable=False),
        sa.Column("return_count", sa.Integer(), nullable=False),
        sa.Column("ad_spend", sa.Numeric(20, 6), nullable=True),
        sa.Column("ad_sales", sa.Numeric(20, 6), nullable=True),
        sa.Column("ad_loss_count", sa.Integer(), nullable=False),
        sa.Column("lines", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "order_count >= 0 AND on_time >= 0 AND late >= 0 AND return_count >= 0",
            name="ck_dashboard_shop_daily_counts",
        ),
        sa.CheckConstraint("gmv >= 0", name="ck_dashboard_shop_daily_gmv"),
        sa.CheckConstraint("ad_loss_count >= 0", name="ck_dashboard_shop_daily_ad_loss_count"),
        sa.CheckConstraint("ad_spend IS NULL OR ad_spend >= 0", name="ck_dashboard_shop_daily_ad_spend"),
        sa.CheckConstraint("ad_sales IS NULL OR ad_sales >= 0", name="ck_dashboard_shop_daily_ad_sales"),
        sa.CheckConstraint(
            "char_length(currency) = 3 AND char_length(book_currency) = 3", name="ck_dashboard_shop_daily_currency"
        ),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_dashboard_shop_daily_shop_id_shop"),
        sa.PrimaryKeyConstraint("id", "stat_date", name="pk_dashboard_shop_daily"),
        sa.UniqueConstraint("tenant_id", "shop_id", "stat_date", "currency", name="uq_dashboard_shop_daily_key"),
        postgresql_partition_by="RANGE (stat_date)",
    )
    op.create_index("ix_dashboard_shop_daily_tenant_id_stat_date", "dashboard_shop_daily", ["tenant_id", "stat_date"])
    op.create_index(
        "ix_dashboard_shop_daily_tenant_id_platform_code_stat_date",
        "dashboard_shop_daily",
        ["tenant_id", "platform_code", "stat_date"],
    )
    _create_partitions("dashboard_shop_daily")
    _tenant_policy("dashboard_shop_daily")
    _grant("dashboard_shop_daily")


def _inventory_daily() -> None:
    op.create_table(
        "dashboard_inventory_daily",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("on_hand_qty", sa.Integer(), nullable=False),
        sa.Column("stockout_sku_count", sa.Integer(), nullable=False),
        sa.Column("below_safe_sku_count", sa.Integer(), nullable=False),
        sa.Column("stale_sku_count", sa.Integer(), nullable=False),
        sa.Column("stale_amounts", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "on_hand_qty >= 0 AND stockout_sku_count >= 0 AND below_safe_sku_count >= 0 AND stale_sku_count >= 0",
            name="ck_dashboard_inventory_daily_counts",
        ),
        sa.PrimaryKeyConstraint("id", "stat_date", name="pk_dashboard_inventory_daily"),
        sa.UniqueConstraint("tenant_id", "stat_date", name="uq_dashboard_inventory_daily_day"),
        postgresql_partition_by="RANGE (stat_date)",
    )
    op.create_index(
        "ix_dashboard_inventory_daily_tenant_id_stat_date",
        "dashboard_inventory_daily",
        ["tenant_id", "stat_date"],
    )
    _create_partitions("dashboard_inventory_daily")
    _tenant_policy("dashboard_inventory_daily")
    _grant("dashboard_inventory_daily")


def upgrade() -> None:
    _shop_daily()
    _inventory_daily()


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dashboard_inventory_daily")
    op.execute("DROP TABLE IF EXISTS dashboard_shop_daily")
