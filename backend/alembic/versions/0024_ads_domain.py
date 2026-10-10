"""广告域 —— M5-03

活动可更新。日指标和关键词按 stat_date 月分区，应用角色不能删除。
V1 不提供广告写接口。

Revision ID: 0024_ads_domain
Revises: 0023_purchase_domain
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

from alembic import op

revision: str = "0024_ads_domain"
down_revision: str | None = "0023_purchase_domain"
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


def upgrade() -> None:
    from app.engines.ads import CAMPAIGN_STATUSES, CAMPAIGN_TYPES, SUGGESTION_CODES

    statuses = _quoted(CAMPAIGN_STATUSES)
    types = _quoted(CAMPAIGN_TYPES)
    suggestions = _quoted(SUGGESTION_CODES)

    op.create_table(
        "ad_campaign",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("platform_campaign_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("campaign_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=True),
        sa.Column("platform_sku_id", sa.String(length=128), nullable=False, server_default=""),
        *_audit_columns(),
        sa.CheckConstraint(f"campaign_type IN ({types})", name="ck_ad_campaign_campaign_type"),
        sa.CheckConstraint(f"status IN ({statuses})", name="ck_ad_campaign_status"),
        sa.CheckConstraint(
            "char_length(btrim(platform_campaign_id)) > 0",
            name="ck_ad_campaign_platform_campaign_id",
        ),
        sa.CheckConstraint("char_length(btrim(name)) > 0", name="ck_ad_campaign_name"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_ad_campaign_currency"),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_ad_campaign_shop_id_shop"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_ad_campaign_sku_id_sku"),
        sa.PrimaryKeyConstraint("id", name="pk_ad_campaign"),
        sa.UniqueConstraint(
            "tenant_id",
            "shop_id",
            "platform_campaign_id",
            name="uq_ad_campaign_platform",
        ),
    )
    op.create_index("ix_ad_campaign_tenant_id", "ad_campaign", ["tenant_id"])
    op.create_index("ix_ad_campaign_created_at", "ad_campaign", ["created_at"])
    op.create_index("ix_ad_campaign_tenant_id_shop_id", "ad_campaign", ["tenant_id", "shop_id"])
    op.create_index("ix_ad_campaign_tenant_id_platform_code", "ad_campaign", ["tenant_id", "platform_code"])
    _tenant_policy("ad_campaign")
    _grant_ledger("ad_campaign")

    op.create_table(
        "ad_metric_daily",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("campaign_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.Column("orders", sa.Integer(), nullable=False),
        sa.Column("spend", sa.Numeric(20, 6), nullable=False),
        sa.Column("sales", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("ctr", sa.Numeric(20, 6), nullable=True),
        sa.Column("acos", sa.Numeric(20, 6), nullable=True),
        sa.Column("roas", sa.Numeric(20, 6), nullable=True),
        sa.Column("cvr", sa.Numeric(20, 6), nullable=True),
        sa.Column("gross_margin", sa.Numeric(20, 6), nullable=True),
        sa.Column("loss_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("suggestion_code", sa.String(length=32), nullable=False, server_default=""),
        sa.CheckConstraint("impressions >= 0 AND clicks >= 0 AND orders >= 0", name="ck_ad_metric_daily_counts"),
        sa.CheckConstraint("clicks <= impressions", name="ck_ad_metric_daily_clicks"),
        sa.CheckConstraint("spend >= 0 AND sales >= 0", name="ck_ad_metric_daily_money"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_ad_metric_daily_currency"),
        sa.CheckConstraint(f"suggestion_code IN ({suggestions})", name="ck_ad_metric_daily_suggestion_code"),
        sa.ForeignKeyConstraint(["campaign_id"], ["ad_campaign.id"], name="fk_ad_metric_daily_campaign_id_ad_campaign"),
        sa.PrimaryKeyConstraint("id", "stat_date", name="pk_ad_metric_daily"),
        sa.UniqueConstraint("tenant_id", "campaign_id", "stat_date", name="uq_ad_metric_daily_day"),
        postgresql_partition_by="RANGE (stat_date)",
    )
    op.create_index("ix_ad_metric_daily_tenant_id_stat_date", "ad_metric_daily", ["tenant_id", "stat_date"])
    op.create_index("ix_ad_metric_daily_tenant_id_campaign_id", "ad_metric_daily", ["tenant_id", "campaign_id"])
    _create_partitions("ad_metric_daily")
    _tenant_policy("ad_metric_daily")
    _grant_ledger("ad_metric_daily", partitions=True)

    op.create_table(
        "ad_keyword_metric",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("campaign_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("keyword", sa.String(length=256), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.Column("orders", sa.Integer(), nullable=False),
        sa.Column("spend", sa.Numeric(20, 6), nullable=False),
        sa.Column("sales", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("suggest_negative", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("impressions >= 0 AND clicks >= 0 AND orders >= 0", name="ck_ad_keyword_metric_counts"),
        sa.CheckConstraint("spend >= 0 AND sales >= 0", name="ck_ad_keyword_metric_money"),
        sa.CheckConstraint("char_length(btrim(keyword)) > 0", name="ck_ad_keyword_metric_keyword"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_ad_keyword_metric_currency"),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["ad_campaign.id"],
            name="fk_ad_keyword_metric_campaign_id_ad_campaign",
        ),
        sa.PrimaryKeyConstraint("id", "stat_date", name="pk_ad_keyword_metric"),
        sa.UniqueConstraint("tenant_id", "campaign_id", "keyword", "stat_date", name="uq_ad_keyword_metric_day"),
        postgresql_partition_by="RANGE (stat_date)",
    )
    op.create_index("ix_ad_keyword_metric_tenant_id_stat_date", "ad_keyword_metric", ["tenant_id", "stat_date"])
    op.create_index("ix_ad_keyword_metric_tenant_id_campaign_id", "ad_keyword_metric", ["tenant_id", "campaign_id"])
    _create_partitions("ad_keyword_metric")
    _tenant_policy("ad_keyword_metric")
    _grant_ledger("ad_keyword_metric", partitions=True)


def downgrade() -> None:
    op.drop_table("ad_keyword_metric")
    op.drop_table("ad_metric_daily")
    op.drop_table("ad_campaign")
