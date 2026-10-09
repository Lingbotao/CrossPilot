"""商品类目代码，供认证要求规则在刊登前对照。

运营自己填写，与认证要求上的字符串一致。不建类目主数据，也不播种。

Revision ID: 0019_spu_category_code
Revises: 0018_tax_certificate
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0019_spu_category_code"
down_revision: str | None = "0018_tax_certificate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("spu", sa.Column("category_code", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "ck_spu_category_code",
        "spu",
        "category_code IS NULL OR char_length(category_code) > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_spu_category_code", "spu", type_="check")
    op.drop_column("spu", "category_code")
