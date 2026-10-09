"""HS 编码库与商品市场绑定 —— M4-01

``hs_code`` 是平台词典，不加 RLS，应用角色只读。
``spu_hs_binding`` 按租户隔离。同一商品同一市场唯一。
描述检索走 pg_trgm 的 GIN 索引。种子只写入有出处的税则子目。

Revision ID: 0017_hs_code
Revises: 0016_stock_transfer_taking
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0017_hs_code"
down_revision: str | None = "0016_stock_transfer_taking"
branch_labels = None
depends_on = None

_APP_ROLE = "crosspilot_app"


def _market_sql() -> str:
    from app.models.locale import CONTENT_MARKETS

    return ", ".join(f"'{item}'" for item in CONTENT_MARKETS)


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


def _grant_read(table: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                GRANT SELECT ON {table} TO {_APP_ROLE};
                REVOKE INSERT, UPDATE, DELETE ON {table} FROM {_APP_ROLE};
            END IF;
        END
        $$;
        """  # noqa: S608
    )


def _grant_binding(table: str) -> None:
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


def _seed_hs_codes() -> None:
    from app.models.hs_code import HS_NOMENCLATURE_SOURCE, HS_SEED, hs_seed_id

    conn = op.get_bind()
    for seq, row in enumerate(HS_SEED, start=1):
        conn.execute(
            sa.text(
                "INSERT INTO hs_code ("
                "id, code, description, parent_code, chapter, level, source"
                ") VALUES ("
                ":id, :code, :description, :parent_code, :chapter, :level, :source"
                ")"
            ),
            {
                "id": hs_seed_id(seq),
                "code": row.code,
                "description": row.description,
                "parent_code": row.parent_code,
                "chapter": row.chapter,
                "level": row.level,
                "source": HS_NOMENCLATURE_SOURCE,
            },
        )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "hs_code",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("parent_code", sa.String(length=16), nullable=True),
        sa.Column("chapter", sa.String(length=2), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("code ~ '^[0-9]{4,10}$'", name="ck_hs_code_code"),
        sa.CheckConstraint("level IN (2, 4, 6, 8, 10)", name="ck_hs_code_level"),
        sa.CheckConstraint("char_length(chapter) = 2 AND chapter ~ '^[0-9]{2}$'", name="ck_hs_code_chapter"),
        sa.CheckConstraint("char_length(btrim(description)) > 0", name="ck_hs_code_description"),
        sa.CheckConstraint("char_length(btrim(source)) > 0", name="ck_hs_code_source"),
        sa.PrimaryKeyConstraint("id", name="pk_hs_code"),
        sa.UniqueConstraint("code", name="uq_hs_code_code"),
    )
    op.create_index("ix_hs_code_created_at", "hs_code", ["created_at"])
    op.create_index("ix_hs_code_chapter", "hs_code", ["chapter"])
    op.create_index(
        "ix_hs_code_description_trgm",
        "hs_code",
        ["description"],
        postgresql_using="gin",
        postgresql_ops={"description": "gin_trgm_ops"},
    )
    _grant_read("hs_code")
    _seed_hs_codes()

    op.create_table(
        "spu_hs_binding",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("spu_id", sa.BigInteger(), nullable=False),
        sa.Column("market", sa.String(length=2), nullable=False),
        sa.Column("hs_code_id", sa.BigInteger(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(f"market IN ({_market_sql()})", name="ck_spu_hs_binding_market"),
        sa.CheckConstraint("char_length(btrim(basis)) > 0", name="ck_spu_hs_binding_basis"),
        sa.ForeignKeyConstraint(["spu_id"], ["spu.id"], name="fk_spu_hs_binding_spu_id_spu"),
        sa.ForeignKeyConstraint(["hs_code_id"], ["hs_code.id"], name="fk_spu_hs_binding_hs_code_id_hs_code"),
        sa.PrimaryKeyConstraint("id", name="pk_spu_hs_binding"),
        sa.UniqueConstraint(
            "tenant_id",
            "spu_id",
            "market",
            name="uq_spu_hs_binding_tenant_id_spu_id_market",
        ),
    )
    op.create_index("ix_spu_hs_binding_tenant_id", "spu_hs_binding", ["tenant_id"])
    op.create_index("ix_spu_hs_binding_created_at", "spu_hs_binding", ["created_at"])
    op.create_index("ix_spu_hs_binding_tenant_id_spu_id", "spu_hs_binding", ["tenant_id", "spu_id"])
    _tenant_policy("spu_hs_binding")
    _grant_binding("spu_hs_binding")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS spu_hs_binding_tenant_isolation ON spu_hs_binding")
    op.drop_index("ix_spu_hs_binding_tenant_id_spu_id", table_name="spu_hs_binding")
    op.drop_table("spu_hs_binding")
    op.drop_index("ix_hs_code_description_trgm", table_name="hs_code")
    op.drop_table("hs_code")
