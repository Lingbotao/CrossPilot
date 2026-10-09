"""多语言文案、术语库与敏感词 —— M3-05

三张都是主数据，可软删除。应用角色没有 DELETE。RLS 手写。

Revision ID: 0014_listing_locale
Revises: 0013_catalog_media
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_listing_locale"
down_revision: str | None = "0013_catalog_media"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"
_LANG_SQL = (
    "'zh-CN', 'zh-TW', 'en', 'id', 'th', 'vi', 'ms', 'es', 'pt', 'de', 'fr', 'it', 'ja'"
)
_MARKET_SQL = "'US', 'CA', 'MX', 'UK', 'GB', 'DE', 'FR', 'IT', 'ES', 'JP', 'AU', 'SG', 'MY', 'TH', 'ID', 'VN', 'PH', 'TW', 'BR'"
_QUALITY_SQL = "'UNTRANSLATED', 'MT_DRAFT', 'REVIEWED', 'PUBLISHED'"


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


def _audit_columns() -> list[sa.Column[sa.DateTime]]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "listing_content",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=False),
        sa.Column("lang", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "bullet_points",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("quality_status", sa.String(length=16), nullable=False, server_default="UNTRANSLATED"),
        *_audit_columns(),
        sa.CheckConstraint(f"lang IN ({_LANG_SQL})", name="ck_listing_content_lang"),
        sa.CheckConstraint(f"quality_status IN ({_QUALITY_SQL})", name="ck_listing_content_quality_status"),
        sa.CheckConstraint("jsonb_typeof(bullet_points) = 'array'", name="ck_listing_content_bullet_points_array"),
        sa.ForeignKeyConstraint(["listing_id"], ["listing.id"], name="fk_listing_content_listing_id_listing"),
        sa.PrimaryKeyConstraint("id", name="pk_listing_content"),
    )
    op.create_index("ix_listing_content_tenant_id", "listing_content", ["tenant_id"])
    op.create_index("ix_listing_content_created_at", "listing_content", ["created_at"])
    op.create_index("ix_listing_content_deleted_at", "listing_content", ["deleted_at"])
    op.create_index("ix_listing_content_tenant_id_listing_id", "listing_content", ["tenant_id", "listing_id"])
    op.create_index(
        "ix_listing_content_tenant_id_quality_status",
        "listing_content",
        ["tenant_id", "quality_status"],
    )
    op.create_index(
        "uq_listing_content_lang_active",
        "listing_content",
        ["tenant_id", "listing_id", "lang"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    _tenant_policy("listing_content")
    _grant("listing_content")

    op.create_table(
        "glossary_term",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("source_lang", sa.String(length=16), nullable=False),
        sa.Column("source_term", sa.String(length=128), nullable=False),
        sa.Column("target_lang", sa.String(length=16), nullable=False),
        sa.Column("target_term", sa.String(length=128), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(f"source_lang IN ({_LANG_SQL})", name="ck_glossary_term_source_lang"),
        sa.CheckConstraint(f"target_lang IN ({_LANG_SQL})", name="ck_glossary_term_target_lang"),
        sa.CheckConstraint(
            "char_length(source_term) > 0 AND char_length(target_term) > 0",
            name="ck_glossary_term_terms",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_glossary_term"),
    )
    op.create_index("ix_glossary_term_tenant_id", "glossary_term", ["tenant_id"])
    op.create_index("ix_glossary_term_created_at", "glossary_term", ["created_at"])
    op.create_index("ix_glossary_term_deleted_at", "glossary_term", ["deleted_at"])
    op.create_index("ix_glossary_term_tenant_id_target_lang", "glossary_term", ["tenant_id", "target_lang"])
    op.execute(
        "CREATE UNIQUE INDEX uq_glossary_term_active ON glossary_term "
        "(tenant_id, source_lang, target_lang, lower(source_term)) "
        "WHERE deleted_at IS NULL"
    )
    _tenant_policy("glossary_term")
    _grant("glossary_term")

    op.create_table(
        "sensitive_term",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("lang", sa.String(length=16), nullable=False),
        sa.Column("keyword", sa.String(length=128), nullable=False),
        sa.Column("suggest_replacement", sa.String(length=128), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(f"market IN ({_MARKET_SQL})", name="ck_sensitive_term_market"),
        sa.CheckConstraint(f"lang IN ({_LANG_SQL})", name="ck_sensitive_term_lang"),
        sa.CheckConstraint("char_length(keyword) > 0", name="ck_sensitive_term_keyword"),
        sa.PrimaryKeyConstraint("id", name="pk_sensitive_term"),
    )
    op.create_index("ix_sensitive_term_tenant_id", "sensitive_term", ["tenant_id"])
    op.create_index("ix_sensitive_term_created_at", "sensitive_term", ["created_at"])
    op.create_index("ix_sensitive_term_deleted_at", "sensitive_term", ["deleted_at"])
    op.create_index(
        "ix_sensitive_term_tenant_id_market_lang",
        "sensitive_term",
        ["tenant_id", "market", "lang"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_sensitive_term_active ON sensitive_term "
        "(tenant_id, market, lang, lower(keyword)) "
        "WHERE deleted_at IS NULL"
    )
    _tenant_policy("sensitive_term")
    _grant("sensitive_term")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS sensitive_term_tenant_isolation ON sensitive_term")
    op.execute("DROP POLICY IF EXISTS glossary_term_tenant_isolation ON glossary_term")
    op.execute("DROP POLICY IF EXISTS listing_content_tenant_isolation ON listing_content")
    op.execute("DROP INDEX IF EXISTS uq_sensitive_term_active")
    op.execute("DROP INDEX IF EXISTS uq_glossary_term_active")
    op.drop_index("ix_sensitive_term_tenant_id_market_lang", table_name="sensitive_term")
    op.drop_table("sensitive_term")
    op.drop_index("ix_glossary_term_tenant_id_target_lang", table_name="glossary_term")
    op.drop_table("glossary_term")
    op.drop_index("uq_listing_content_lang_active", table_name="listing_content")
    op.drop_index("ix_listing_content_tenant_id_quality_status", table_name="listing_content")
    op.drop_index("ix_listing_content_tenant_id_listing_id", table_name="listing_content")
    op.drop_table("listing_content")
