"""订单状态机 —— M2-06

``platform_status_mapping`` 是平台字典，不加 RLS。
``order_status_log`` 是租户流水，按月分区，应用角色只能追加。
``sales_order.unified_status`` 收成九态，并加上检查约束。

Revision ID: 0006_order_status
Revises: 0005_order_sync
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from alembic import op

from app.engines.order_status import UNIFIED_STATUS_SQL

revision: str = "0006_order_status"
down_revision: str | None = "0005_order_sync"
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


def upgrade() -> None:
    op.execute(
        """
        UPDATE sales_order SET unified_status = CASE unified_status
            WHEN 'PENDING_PAYMENT' THEN 'PENDING'
            WHEN 'TO_SHIP' THEN 'PAID'
            WHEN 'IN_TRANSIT' THEN 'SHIPPED'
            WHEN 'RETURNING' THEN 'REFUNDING'
            ELSE unified_status
        END
        WHERE unified_status IN ('PENDING_PAYMENT', 'TO_SHIP', 'IN_TRANSIT', 'RETURNING')
        """
    )
    op.execute(
        f"""
        UPDATE sales_order SET unified_status = 'PENDING'
        WHERE unified_status NOT IN ({UNIFIED_STATUS_SQL})
        """
    )
    op.create_check_constraint(
        "ck_sales_order_unified_status",
        "sales_order",
        f"unified_status IN ({UNIFIED_STATUS_SQL})",
    )

    op.create_table(
        "platform_status_mapping",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("platform_status", sa.String(length=64), nullable=False),
        sa.Column("unified_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_platform_status_mapping"),
        sa.UniqueConstraint(
            "platform_code",
            "platform_status",
            name="uq_platform_status_mapping_platform_code_platform_status",
        ),
        sa.CheckConstraint(
            f"unified_status IN ({UNIFIED_STATUS_SQL})",
            name="ck_platform_status_mapping_unified_status",
        ),
    )
    op.create_index(
        "ix_platform_status_mapping_created_at",
        "platform_status_mapping",
        ["created_at"],
    )
    op.create_index(
        "ix_platform_status_mapping_platform_code",
        "platform_status_mapping",
        ["platform_code"],
    )

    op.create_table(
        "order_status_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("platform_status", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("operator_id", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", "created_at", name="pk_order_status_log"),
        sa.ForeignKeyConstraint(["order_id"], ["sales_order.id"], name="fk_order_status_log_order_id_sales_order"),
        sa.CheckConstraint(f"to_status IN ({UNIFIED_STATUS_SQL})", name="ck_order_status_log_to_status"),
        sa.CheckConstraint(
            f"from_status IS NULL OR from_status IN ({UNIFIED_STATUS_SQL})",
            name="ck_order_status_log_from_status",
        ),
        sa.CheckConstraint(
            "source IN ('SYSTEM', 'WEBHOOK', 'MANUAL')",
            name="ck_order_status_log_source",
        ),
        postgresql_partition_by="RANGE (created_at)",
    )
    op.create_index("ix_order_status_log_tenant_id", "order_status_log", ["tenant_id"])
    op.create_index(
        "ix_order_status_log_tenant_id_order_id_created_at",
        "order_status_log",
        ["tenant_id", "order_id", "created_at"],
    )
    _create_log_partitions()
    _exec_script(*_tenant_policy_sql("order_status_log"))

    op.execute(
        f"""
        DO $$
        DECLARE
            part regclass;
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE ON platform_status_mapping TO {_APP_ROLE};
                GRANT SELECT, INSERT ON order_status_log TO {_APP_ROLE};
                -- 初始化脚本的默认权限会带上 DELETE。流水表必须收回。
                REVOKE DELETE ON sales_order FROM {_APP_ROLE};
                REVOKE UPDATE, DELETE ON order_status_log FROM {_APP_ROLE};
                FOR part IN
                    SELECT inhrelid
                    FROM pg_inherits
                    WHERE inhparent = 'order_status_log'::regclass
                LOOP
                    EXECUTE format(
                        'GRANT SELECT, INSERT ON TABLE %s TO {_APP_ROLE}',
                        part
                    );
                    EXECUTE format(
                        'REVOKE UPDATE, DELETE ON TABLE %s FROM {_APP_ROLE}',
                        part
                    );
                END LOOP;
            END IF;
        END
        $$;
        """
    )


def _create_log_partitions() -> None:
    today = date.today().replace(day=1)
    start = _add_months(today, -_PARTITION_MONTHS_BACK)
    for i in range(_PARTITION_MONTHS_BACK + _PARTITION_MONTHS_FORWARD + 1):
        lower = _add_months(start, i)
        upper = _add_months(start, i + 1)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS order_status_log_{lower:%Y_%m} "
            f"PARTITION OF order_status_log FOR VALUES FROM ('{lower.isoformat()}') TO ('{upper.isoformat()}')"
        )


def downgrade() -> None:
    today = date.today().replace(day=1)
    start = _add_months(today, -_PARTITION_MONTHS_BACK)
    for i in range(_PARTITION_MONTHS_BACK + _PARTITION_MONTHS_FORWARD + 1):
        lower = _add_months(start, i)
        op.execute(f"DROP TABLE IF EXISTS order_status_log_{lower:%Y_%m}")
    op.drop_table("order_status_log")
    op.drop_table("platform_status_mapping")
    op.drop_constraint("ck_sales_order_unified_status", "sales_order", type_="check")
