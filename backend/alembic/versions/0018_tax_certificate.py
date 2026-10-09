"""税率版本、认证台账、认证要求与提醒去重 —— M4-02 / M4-03

四张表都按租户隔离，应用角色不能删除。不写入任何税率种子。

Revision ID: 0018_tax_certificate
Revises: 0017_hs_code
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0018_tax_certificate"
down_revision: str | None = "0017_hs_code"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"


def _market_sql() -> str:
    from app.models.locale import CONTENT_MARKETS

    return ", ".join(f"'{item}'" for item in CONTENT_MARKETS)


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


def _indexes(table: str, extra: list[tuple[str, list[str]]]) -> None:
    op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
    op.create_index(f"ix_{table}_created_at", table, ["created_at"])
    for name, columns in extra:
        op.create_index(name, table, columns)


def upgrade() -> None:
    from app.models.compliance import CERT_TYPES, NOTICE_KINDS, NOTICE_LEVELS, TAX_RULE_STATUSES, TAX_TYPES

    markets = _market_sql()
    tax_types = _quoted(TAX_TYPES)
    statuses = _quoted(TAX_RULE_STATUSES)
    cert_types = _quoted(CERT_TYPES)
    kinds = _quoted(NOTICE_KINDS)
    levels = _quoted(NOTICE_LEVELS)

    op.create_table(
        "country_tax_rule",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("tax_type", sa.String(length=16), nullable=False),
        sa.Column("hs_code_pattern", sa.String(length=16), nullable=False, server_default="*"),
        sa.Column("rate", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("basis_numerator", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("basis_denominator", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("threshold_amount", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("threshold_currency", sa.CHAR(length=3), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("verified_by", sa.BigInteger(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(f"country IN ({markets})", name="ck_country_tax_rule_country"),
        sa.CheckConstraint(f"tax_type IN ({tax_types})", name="ck_country_tax_rule_tax_type"),
        sa.CheckConstraint(f"status IN ({statuses})", name="ck_country_tax_rule_status"),
        sa.CheckConstraint(
            "hs_code_pattern = '*' OR hs_code_pattern ~ '^[0-9]{2,10}$'",
            name="ck_country_tax_rule_hs_code_pattern",
        ),
        sa.CheckConstraint("rate >= 0 AND rate <= 10", name="ck_country_tax_rule_rate"),
        sa.CheckConstraint(
            "basis_numerator >= 1 AND basis_denominator >= 1",
            name="ck_country_tax_rule_basis",
        ),
        sa.CheckConstraint(
            "(threshold_amount IS NULL AND threshold_currency IS NULL) "
            "OR (threshold_amount IS NOT NULL AND threshold_currency IS NOT NULL AND threshold_amount >= 0)",
            name="ck_country_tax_rule_threshold",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_country_tax_rule_effective",
        ),
        sa.CheckConstraint("char_length(btrim(source)) > 0", name="ck_country_tax_rule_source"),
        sa.CheckConstraint("version >= 1", name="ck_country_tax_rule_version"),
        sa.PrimaryKeyConstraint("id", name="pk_country_tax_rule"),
        sa.UniqueConstraint(
            "tenant_id",
            "country",
            "tax_type",
            "hs_code_pattern",
            "version",
            name="uq_tax_rule_key_version",
        ),
    )
    _indexes(
        "country_tax_rule",
        [("ix_country_tax_rule_tenant_id_country", ["tenant_id", "country"])],
    )
    _tenant_policy("country_tax_rule")
    _grant_ledger("country_tax_rule")

    op.create_table(
        "compliance_certificate",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("cert_type", sa.String(length=16), nullable=False),
        sa.Column("cert_no", sa.String(length=128), nullable=False),
        sa.Column("issued_at", sa.Date(), nullable=False),
        sa.Column("expires_at", sa.Date(), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=True),
        sa.Column("content_type", sa.String(length=64), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(f"market IN ({markets})", name="ck_compliance_certificate_market"),
        sa.CheckConstraint(f"cert_type IN ({cert_types})", name="ck_compliance_certificate_cert_type"),
        sa.CheckConstraint("char_length(btrim(cert_no)) > 0", name="ck_compliance_certificate_cert_no"),
        sa.CheckConstraint("expires_at >= issued_at", name="ck_compliance_certificate_expires"),
        sa.CheckConstraint(
            "(object_key IS NULL AND content_type IS NULL) OR (object_key IS NOT NULL AND content_type IS NOT NULL)",
            name="ck_compliance_certificate_file",
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_compliance_certificate_sku_id_sku"),
        sa.PrimaryKeyConstraint("id", name="pk_compliance_certificate"),
        sa.UniqueConstraint(
            "tenant_id",
            "sku_id",
            "market",
            "cert_type",
            "cert_no",
            name="uq_certificate_natural",
        ),
    )
    _indexes(
        "compliance_certificate",
        [
            ("ix_compliance_certificate_tenant_id_sku_id", ["tenant_id", "sku_id"]),
            ("ix_compliance_certificate_tenant_id_expires_at", ["tenant_id", "expires_at"]),
        ],
    )
    _tenant_policy("compliance_certificate")
    _grant_ledger("compliance_certificate")

    op.create_table(
        "cert_requirement_rule",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("category_code", sa.String(length=64), nullable=False),
        sa.Column("cert_type", sa.String(length=16), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        *_audit_columns(),
        sa.CheckConstraint(f"market IN ({markets})", name="ck_cert_requirement_rule_market"),
        sa.CheckConstraint(f"cert_type IN ({cert_types})", name="ck_cert_requirement_rule_cert_type"),
        sa.CheckConstraint(f"status IN ({statuses})", name="ck_cert_requirement_rule_status"),
        sa.CheckConstraint(
            "char_length(btrim(category_code)) > 0",
            name="ck_cert_requirement_rule_category_code",
        ),
        sa.CheckConstraint("char_length(btrim(source)) > 0", name="ck_cert_requirement_rule_source"),
        sa.PrimaryKeyConstraint("id", name="pk_cert_requirement_rule"),
        sa.UniqueConstraint(
            "tenant_id",
            "market",
            "category_code",
            "cert_type",
            name="uq_cert_requirement",
        ),
    )
    _indexes(
        "cert_requirement_rule",
        [("ix_cert_requirement_rule_tenant_id_market", ["tenant_id", "market"])],
    )
    _tenant_policy("cert_requirement_rule")
    _grant_ledger("cert_requirement_rule")

    op.create_table(
        "compliance_notice",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("ref_id", sa.BigInteger(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(f"kind IN ({kinds})", name="ck_compliance_notice_kind"),
        sa.CheckConstraint(f"level IN ({levels})", name="ck_compliance_notice_level"),
        sa.CheckConstraint("char_length(btrim(summary)) > 0", name="ck_compliance_notice_summary"),
        sa.PrimaryKeyConstraint("id", name="pk_compliance_notice"),
        sa.UniqueConstraint(
            "tenant_id",
            "kind",
            "level",
            "ref_id",
            "due_on",
            name="uq_compliance_notice",
        ),
    )
    _indexes(
        "compliance_notice",
        [("ix_compliance_notice_tenant_id_kind", ["tenant_id", "kind"])],
    )
    _tenant_policy("compliance_notice")
    _grant_ledger("compliance_notice")


def downgrade() -> None:
    for table in (
        "compliance_notice",
        "cert_requirement_rule",
        "compliance_certificate",
        "country_tax_rule",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
