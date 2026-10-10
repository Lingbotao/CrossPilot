"""客服域 —— M5-05

消息、工单和跟进不能删除。模板用软删除，应用角色同样没有 DELETE。

Revision ID: 0026_cs_domain
Revises: 0025_dashboard_domain
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0026_cs_domain"
down_revision: str | None = "0025_dashboard_domain"
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


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
    ]


def upgrade() -> None:
    from app.engines.cs import MESSAGE_STATUSES, TEMPLATE_SCENES, TICKET_STATUSES, TICKET_TYPES

    message_status = _quoted(MESSAGE_STATUSES)
    scenes = _quoted(TEMPLATE_SCENES)
    ticket_status = _quoted(TICKET_STATUSES)
    ticket_types = _quoted(TICKET_TYPES)

    op.create_table(
        "cs_message",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("platform_code", sa.String(length=32), nullable=False),
        sa.Column("platform_message_id", sa.String(length=128), nullable=False),
        sa.Column("platform_order_id", sa.String(length=128), nullable=True),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("buyer_id", sa.String(length=128), nullable=True),
        sa.Column("buyer_name", sa.String(length=128), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("lang", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sla_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("console_url", sa.String(length=512), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(f"status IN ({message_status})", name="ck_cs_message_status"),
        sa.CheckConstraint(
            "char_length(btrim(platform_message_id)) > 0",
            name="ck_cs_message_platform_message_id",
        ),
        sa.CheckConstraint("char_length(btrim(content)) > 0", name="ck_cs_message_content"),
        sa.CheckConstraint("console_url LIKE 'https://%'", name="ck_cs_message_console_url"),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_cs_message_shop_id_shop"),
        sa.ForeignKeyConstraint(["order_id"], ["sales_order.id"], name="fk_cs_message_order_id_sales_order"),
        sa.PrimaryKeyConstraint("id", name="pk_cs_message"),
    )
    op.create_index("ix_cs_message_tenant_id", "cs_message", ["tenant_id"])
    op.create_index("ix_cs_message_created_at", "cs_message", ["created_at"])
    op.create_index(
        "uq_cs_message_shop_platform_message",
        "cs_message",
        ["tenant_id", "shop_id", "platform_message_id"],
        unique=True,
    )
    op.create_index("ix_cs_message_tenant_id_sla_deadline", "cs_message", ["tenant_id", "sla_deadline"])
    op.create_index("ix_cs_message_tenant_id_status", "cs_message", ["tenant_id", "status"])
    _tenant_policy("cs_message")
    _grant("cs_message")

    op.create_table(
        "cs_template",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("scene", sa.String(length=16), nullable=False),
        sa.Column("lang", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(f"scene IN ({scenes})", name="ck_cs_template_scene"),
        sa.CheckConstraint("char_length(btrim(name)) > 0", name="ck_cs_template_name"),
        sa.CheckConstraint("char_length(btrim(body)) > 0", name="ck_cs_template_body"),
        sa.PrimaryKeyConstraint("id", name="pk_cs_template"),
    )
    op.create_index("ix_cs_template_tenant_id", "cs_template", ["tenant_id"])
    op.create_index("ix_cs_template_created_at", "cs_template", ["created_at"])
    op.create_index("ix_cs_template_deleted_at", "cs_template", ["deleted_at"])
    op.create_index(
        "uq_cs_template_live",
        "cs_template",
        ["tenant_id", "scene", "lang", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _tenant_policy("cs_template")
    _grant("cs_template")

    op.create_table(
        "cs_ticket",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("return_order_id", sa.BigInteger(), nullable=True),
        sa.Column("buyer_id", sa.String(length=128), nullable=True),
        sa.Column("buyer_name", sa.String(length=128), nullable=True),
        sa.Column("ticket_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("assignee_user_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(f"ticket_type IN ({ticket_types})", name="ck_cs_ticket_ticket_type"),
        sa.CheckConstraint(f"status IN ({ticket_status})", name="ck_cs_ticket_status"),
        sa.CheckConstraint("char_length(btrim(title)) > 0", name="ck_cs_ticket_title"),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_cs_ticket_shop_id_shop"),
        sa.ForeignKeyConstraint(["message_id"], ["cs_message.id"], name="fk_cs_ticket_message_id_cs_message"),
        sa.ForeignKeyConstraint(["order_id"], ["sales_order.id"], name="fk_cs_ticket_order_id_sales_order"),
        sa.ForeignKeyConstraint(
            ["return_order_id"],
            ["return_order.id"],
            name="fk_cs_ticket_return_order_id_return_order",
        ),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["sys_user.id"], name="fk_cs_ticket_assignee_user_id_sys_user"),
        sa.PrimaryKeyConstraint("id", name="pk_cs_ticket"),
    )
    op.create_index("ix_cs_ticket_tenant_id", "cs_ticket", ["tenant_id"])
    op.create_index("ix_cs_ticket_created_at", "cs_ticket", ["created_at"])
    op.create_index("ix_cs_ticket_tenant_id_status", "cs_ticket", ["tenant_id", "status"])
    op.create_index("ix_cs_ticket_tenant_id_order_id", "cs_ticket", ["tenant_id", "order_id"])
    _tenant_policy("cs_ticket")
    _grant("cs_ticket")

    op.create_table(
        "cs_ticket_note",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint("char_length(btrim(body)) > 0", name="ck_cs_ticket_note_body"),
        sa.ForeignKeyConstraint(["ticket_id"], ["cs_ticket.id"], name="fk_cs_ticket_note_ticket_id_cs_ticket"),
        sa.PrimaryKeyConstraint("id", name="pk_cs_ticket_note"),
    )
    op.create_index("ix_cs_ticket_note_tenant_id", "cs_ticket_note", ["tenant_id"])
    op.create_index("ix_cs_ticket_note_created_at", "cs_ticket_note", ["created_at"])
    op.create_index(
        "ix_cs_ticket_note_tenant_id_ticket_id_created_at",
        "cs_ticket_note",
        ["tenant_id", "ticket_id", "created_at"],
    )
    _tenant_policy("cs_ticket_note")
    _grant("cs_ticket_note")


def downgrade() -> None:
    op.drop_table("cs_ticket_note")
    op.drop_table("cs_ticket")
    op.drop_table("cs_template")
    op.drop_table("cs_message")
