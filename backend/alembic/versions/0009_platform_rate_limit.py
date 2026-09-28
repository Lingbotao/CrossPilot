"""平台限流配额 —— M2 收尾

``platform_rate_limit`` 是平台字典，不加 RLS。
应用角色可以改配额，不能删行：删掉会静默退回代码默认值。

Revision ID: 0009_platform_rate_limit
Revises: 0008_order_desk
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009_platform_rate_limit"
down_revision: str | None = "0008_order_desk"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"


def upgrade() -> None:
    op.create_table(
        "platform_rate_limit",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("qps", sa.Integer(), nullable=False),
        sa.Column("burst", sa.Integer(), nullable=False),
        sa.Column("batch_limit", sa.Integer(), nullable=False),
        sa.Column("concurrency", sa.Integer(), nullable=True),
        sa.Column("daily_quota", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_platform_rate_limit"),
        sa.UniqueConstraint("platform_code", name="uq_platform_rate_limit_platform_code"),
        sa.CheckConstraint(
            "dimension IN ('shop', 'per_shop', 'app', 'per_app')",
            name="ck_platform_rate_limit_dimension",
        ),
        sa.CheckConstraint("qps > 0", name="ck_platform_rate_limit_qps"),
        sa.CheckConstraint("burst > 0", name="ck_platform_rate_limit_burst"),
        sa.CheckConstraint("batch_limit > 0", name="ck_platform_rate_limit_batch_limit"),
        sa.CheckConstraint(
            "concurrency IS NULL OR concurrency > 0",
            name="ck_platform_rate_limit_concurrency",
        ),
        sa.CheckConstraint(
            "daily_quota IS NULL OR daily_quota > 0",
            name="ck_platform_rate_limit_daily_quota",
        ),
    )
    op.create_index("ix_platform_rate_limit_created_at", "platform_rate_limit", ["created_at"])
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE ON platform_rate_limit TO {_APP_ROLE};
                REVOKE DELETE ON platform_rate_limit FROM {_APP_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                REVOKE SELECT, INSERT, UPDATE ON platform_rate_limit FROM {_APP_ROLE};
            END IF;
        END
        $$;
        """
    )
    op.drop_index("ix_platform_rate_limit_created_at", table_name="platform_rate_limit")
    op.drop_table("platform_rate_limit")
