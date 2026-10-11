"""报表导出任务 —— M5-06

导出记录不能删除。应用角色没有 DELETE。

Revision ID: 0027_report_export
Revises: 0026_cs_domain
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0027_report_export"
down_revision: str | None = "0026_cs_domain"
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


def upgrade() -> None:
    from app.engines.report import EXPORT_DATASETS, EXPORT_FORMATS, EXPORT_STATUSES

    datasets = _quoted(EXPORT_DATASETS)
    formats = _quoted(EXPORT_FORMATS)
    statuses = _quoted(EXPORT_STATUSES)

    op.create_table(
        "report_export",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("dataset", sa.String(length=32), nullable=False),
        sa.Column("file_format", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("include_cost", sa.Boolean(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("object_key", sa.String(length=255), nullable=True),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(f"dataset IN ({datasets})", name="ck_report_export_dataset"),
        sa.CheckConstraint(f"file_format IN ({formats})", name="ck_report_export_file_format"),
        sa.CheckConstraint(f"status IN ({statuses})", name="ck_report_export_status"),
        sa.CheckConstraint("date_from <= date_to", name="ck_report_export_date_window"),
        sa.CheckConstraint("row_count IS NULL OR row_count >= 0", name="ck_report_export_row_count"),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="ck_report_export_idempotency_key",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_report_export"),
    )
    op.create_index("ix_report_export_tenant_id", "report_export", ["tenant_id"])
    op.create_index("ix_report_export_created_at", "report_export", ["created_at"])
    op.create_index("ix_report_export_tenant_id_status", "report_export", ["tenant_id", "status"])
    op.create_index("ix_report_export_tenant_id_created_at", "report_export", ["tenant_id", "created_at"])
    op.create_index(
        "uq_report_export_tenant_idempotency",
        "report_export",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_report_export_tenant_active",
        "report_export",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )
    _tenant_policy("report_export")
    _grant("report_export")


def downgrade() -> None:
    op.drop_table("report_export")
