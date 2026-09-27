"""订单审核、备注、改址历史、退货单 —— M2-10

审核规则可删除。备注、改址历史、退货单是流水，应用角色不能 DELETE。
``sales_order.review_status`` 默认自动通过，避免旧订单全部落入待审。

Revision ID: 0008_order_desk
Revises: 0007_order_fulfillment
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008_order_desk"
down_revision: str | None = "0007_order_fulfillment"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"


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
    op.add_column(
        "sales_order",
        sa.Column(
            "review_status",
            sa.String(length=16),
            nullable=False,
            server_default="AUTO_PASSED",
        ),
    )
    op.create_check_constraint(
        "ck_sales_order_review_status",
        "sales_order",
        "review_status IN ('AUTO_PASSED', 'PENDING', 'APPROVED', 'REJECTED')",
    )

    op.create_table(
        "order_review_rule",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount_gt", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_order_review_rule"),
        sa.UniqueConstraint("tenant_id", "currency", name="uq_order_review_rule_tenant_id_currency"),
    )
    op.create_index("ix_order_review_rule_tenant_id", "order_review_rule", ["tenant_id"])
    op.create_index("ix_order_review_rule_created_at", "order_review_rule", ["created_at"])
    _exec_script(*_tenant_policy_sql("order_review_rule"))

    op.create_table(
        "order_note",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_order_note"),
        sa.ForeignKeyConstraint(["order_id"], ["sales_order.id"], name="fk_order_note_order_id_sales_order"),
    )
    op.create_index("ix_order_note_tenant_id", "order_note", ["tenant_id"])
    op.create_index(
        "ix_order_note_tenant_id_order_id_created_at",
        "order_note",
        ["tenant_id", "order_id", "created_at"],
    )
    _exec_script(*_tenant_policy_sql("order_note"))

    op.create_table(
        "order_address_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("before_address", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("after_address", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_order_address_log"),
        sa.ForeignKeyConstraint(["order_id"], ["sales_order.id"], name="fk_order_address_log_order_id_sales_order"),
        sa.CheckConstraint("status IN ('SUCCEEDED', 'FAILED')", name="ck_order_address_log_status"),
    )
    op.create_index("ix_order_address_log_tenant_id", "order_address_log", ["tenant_id"])
    op.create_index(
        "ix_order_address_log_tenant_id_order_id_created_at",
        "order_address_log",
        ["tenant_id", "order_id", "created_at"],
    )
    _exec_script(*_tenant_policy_sql("order_address_log"))

    op.create_table(
        "return_order",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("refund_amount", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("restock_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("restock_sellable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("restock_status", sa.String(length=16), nullable=False, server_default="NONE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_return_order"),
        sa.ForeignKeyConstraint(["order_id"], ["sales_order.id"], name="fk_return_order_order_id_sales_order"),
        sa.CheckConstraint(
            "status IN ('REQUESTED', 'APPROVED', 'REJECTED', 'REFUNDED')",
            name="ck_return_order_status",
        ),
        sa.CheckConstraint(
            "restock_status IN ('NONE', 'DEFERRED')",
            name="ck_return_order_restock_status",
        ),
    )
    op.create_index("ix_return_order_tenant_id", "return_order", ["tenant_id"])
    op.create_index("ix_return_order_created_at", "return_order", ["created_at"])
    op.create_index("ix_return_order_tenant_id_order_id", "return_order", ["tenant_id", "order_id"])
    op.create_index("ix_return_order_tenant_id_status", "return_order", ["tenant_id", "status"])
    _exec_script(*_tenant_policy_sql("return_order"))

    _exec_script(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON order_review_rule TO {_APP_ROLE}",
        f"GRANT SELECT, INSERT ON order_note TO {_APP_ROLE}",
        f"REVOKE UPDATE, DELETE ON order_note FROM {_APP_ROLE}",
        f"GRANT SELECT, INSERT ON order_address_log TO {_APP_ROLE}",
        f"REVOKE UPDATE, DELETE ON order_address_log FROM {_APP_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE ON return_order TO {_APP_ROLE}",
        f"REVOKE DELETE ON return_order FROM {_APP_ROLE}",
    )


def downgrade() -> None:
    _exec_script(
        "DROP POLICY IF EXISTS return_order_tenant_isolation ON return_order",
        "DROP POLICY IF EXISTS order_address_log_tenant_isolation ON order_address_log",
        "DROP POLICY IF EXISTS order_note_tenant_isolation ON order_note",
        "DROP POLICY IF EXISTS order_review_rule_tenant_isolation ON order_review_rule",
    )
    op.drop_table("return_order")
    op.drop_table("order_address_log")
    op.drop_table("order_note")
    op.drop_table("order_review_rule")
    op.drop_constraint("ck_sales_order_review_status", "sales_order", type_="check")
    op.drop_column("sales_order", "review_status")
