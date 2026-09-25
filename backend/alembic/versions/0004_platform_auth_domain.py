"""平台与授权域 —— Alembic 迁移批次 2（PRD 9.2 ②）

创建 platform / shop / shop_credential / shop_group / sync_task / platform_api_log。
``platform_api_log`` 建表即按月分区。租户表补 RLS。应用角色只获得运行时需要的权限。

Revision ID: 0004_platform_auth_domain
Revises: 0003_identity_rbac_audit
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_platform_auth_domain"
down_revision: str | None = "0003_identity_rbac_audit"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"
_PARTITION_MONTHS_BACK = 1
_PARTITION_MONTHS_FORWARD = 12
_TENANT_TABLES = ("shop", "shop_credential", "shop_group", "sync_task", "platform_api_log")


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _exec_script(*statements: str) -> None:
    for stmt in statements:
        op.execute(stmt)


def _tenant_policy_sql(table: str) -> list[str]:
    setting = "NULLIF(current_setting('app.current_tenant', true), '')::bigint"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id = {setting})
            WITH CHECK (tenant_id = {setting})
        """,
    ]


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "platform",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_platform"),
        sa.UniqueConstraint("code", name="uq_platform_code"),
    )

    op.create_table(
        "shop",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("site_code", sa.String(length=8), nullable=False),
        sa.Column("shop_name", sa.String(length=128), nullable=False),
        sa.Column("platform_shop_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.SmallInteger(), nullable=True),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("unbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_retain_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_shop"),
        sa.ForeignKeyConstraint(["platform_code"], ["platform.code"], name="fk_shop_platform_code_platform"),
    )
    op.create_index("ix_shop_tenant_id", "shop", ["tenant_id"])
    op.create_index("ix_shop_tenant_id_status", "shop", ["tenant_id", "status"])
    op.create_index("ix_shop_deleted_at", "shop", ["deleted_at"])
    op.create_index("ix_shop_created_at", "shop", ["created_at"])
    op.create_index(
        "uq_shop_platform_identity",
        "shop",
        ["tenant_id", "platform_code", "site_code", "platform_shop_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "shop_credential",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("access_token_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token_enc", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("refresh_fail_count", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_error", sa.String(length=512), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_shop_credential"),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_shop_credential_shop_id_shop"),
        sa.UniqueConstraint("shop_id", name="uq_shop_credential_shop_id"),
    )
    op.create_index("ix_shop_credential_tenant_id", "shop_credential", ["tenant_id"])
    op.create_index("ix_shop_credential_tenant_id_expires_at", "shop_credential", ["tenant_id", "expires_at"])
    op.create_index("ix_shop_credential_created_at", "shop_credential", ["created_at"])

    op.create_table(
        "shop_group",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("shop_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False, server_default=sa.text("'{}'::bigint[]")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_shop_group"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_shop_group_tenant_id_name"),
    )
    op.create_index("ix_shop_group_tenant_id", "shop_group", ["tenant_id"])
    op.create_index("ix_shop_group_deleted_at", "shop_group", ["deleted_at"])
    op.create_index("ix_shop_group_created_at", "shop_group", ["created_at"])

    op.create_table(
        "sync_task",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("module", sa.String(length=32), nullable=False),
        sa.Column("trigger_type", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.String(length=512), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_sync_task"),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_sync_task_shop_id_shop"),
    )
    op.create_index("ix_sync_task_tenant_id", "sync_task", ["tenant_id"])
    op.create_index("ix_sync_task_tenant_id_shop_id_created_at", "sync_task", ["tenant_id", "shop_id", "created_at"])
    op.create_index("ix_sync_task_tenant_id_status", "sync_task", ["tenant_id", "status"])
    op.create_index("ix_sync_task_created_at", "sync_task", ["created_at"])

    op.create_table(
        "platform_api_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("retry_count", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", "created_at", name="pk_platform_api_log"),
        postgresql_partition_by="RANGE (created_at)",
    )
    op.create_index("ix_platform_api_log_tenant_id", "platform_api_log", ["tenant_id"])
    op.create_index("ix_platform_api_log_tenant_id_created_at", "platform_api_log", ["tenant_id", "created_at"])
    op.create_index(
        "ix_platform_api_log_tenant_id_shop_id_created_at",
        "platform_api_log",
        ["tenant_id", "shop_id", "created_at"],
    )
    _create_api_log_partitions()

    op.execute(
        """
        INSERT INTO platform (id, code, name, status) VALUES
            (1, 'amazon', 'Amazon', 1),
            (2, 'shopee', 'Shopee', 1),
            (3, 'lazada', 'Lazada', 1),
            (4, 'tiktok', 'TikTok Shop', 1)
        ON CONFLICT (code) DO NOTHING
        """
    )

    for table in _TENANT_TABLES:
        _exec_script(*_tenant_policy_sql(table))

    op.execute(
        f"""
        DO $$
        DECLARE
            part regclass;
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                GRANT SELECT ON platform TO {_APP_ROLE};
                GRANT SELECT, INSERT, UPDATE, DELETE ON shop, shop_credential, shop_group TO {_APP_ROLE};
                GRANT SELECT, INSERT, UPDATE ON sync_task TO {_APP_ROLE};
                GRANT SELECT, INSERT, UPDATE ON platform_api_log TO {_APP_ROLE};
                FOR part IN
                    SELECT inhrelid
                    FROM pg_inherits
                    WHERE inhparent = 'platform_api_log'::regclass
                LOOP
                    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON TABLE %s TO {_APP_ROLE}', part);
                END LOOP;
            END IF;
        END
        $$;
        """
    )


def _create_api_log_partitions() -> None:
    today = date.today().replace(day=1)
    start = _add_months(today, -_PARTITION_MONTHS_BACK)
    for i in range(_PARTITION_MONTHS_BACK + _PARTITION_MONTHS_FORWARD + 1):
        lower = _add_months(start, i)
        upper = _add_months(start, i + 1)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS platform_api_log_{lower:%Y_%m} "
            f"PARTITION OF platform_api_log FOR VALUES FROM ('{lower.isoformat()}') TO ('{upper.isoformat()}')"
        )


def downgrade() -> None:
    today = date.today().replace(day=1)
    start = _add_months(today, -_PARTITION_MONTHS_BACK)
    for i in range(_PARTITION_MONTHS_BACK + _PARTITION_MONTHS_FORWARD + 1):
        lower = _add_months(start, i)
        op.execute(f"DROP TABLE IF EXISTS platform_api_log_{lower:%Y_%m}")
    op.drop_table("platform_api_log")
    op.drop_table("sync_task")
    op.drop_table("shop_group")
    op.drop_table("shop_credential")
    op.drop_table("shop")
    op.drop_table("platform")
