"""拆开 ``login_log`` 的 RLS：允许未归属租户的失败登录写入。

0001 给 ``login_log`` 套了与其它租户表相同的 FOR ALL 策略
（``WITH CHECK (tenant_id = current_setting)``）。失败登录发生在绑定
``app.current_tenant`` **之前**，且 ``tenant_id`` 为 NULL：
``NULL = NULL`` 在 SQL 里是 unknown，INSERT 被拒，登录失败会变成 500，
锁定计数也一并回滚。

本迁移只改 ``login_log``：

- SELECT：本租户行；未绑定租户时只能看见 ``tenant_id IS NULL``
  （ORM ``INSERT ... RETURNING`` 必须过 SELECT，否则失败登录仍 500）
- UPDATE / DELETE：仍只允许本租户行（NULL 行租户改不到）
- INSERT：允许 ``tenant_id IS NULL`` 或写入本租户

Revision ID: 0002_login_log_insert_policy
Revises: 0001_tenant_permission_domain
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_login_log_insert_policy"
down_revision: str | None = "0001_tenant_permission_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SETTING = "NULLIF(current_setting('app.current_tenant', true), '')::bigint"


def _exec(*statements: str) -> None:
    """逐条执行：asyncpg 一次不能塞多条命令。"""
    for stmt in statements:
        op.execute(stmt)


def upgrade() -> None:
    _exec(
        "DROP POLICY IF EXISTS login_log_tenant_isolation ON login_log",
        f"""
        CREATE POLICY login_log_select ON login_log
            FOR SELECT
            USING (
                tenant_id = {_SETTING}
                OR (tenant_id IS NULL AND {_SETTING} IS NULL)
            )
        """,
        f"""
        CREATE POLICY login_log_update ON login_log
            FOR UPDATE
            USING (tenant_id = {_SETTING})
            WITH CHECK (tenant_id = {_SETTING})
        """,
        f"""
        CREATE POLICY login_log_delete ON login_log
            FOR DELETE
            USING (tenant_id = {_SETTING})
        """,
        f"""
        CREATE POLICY login_log_insert ON login_log
            FOR INSERT
            WITH CHECK (tenant_id IS NULL OR tenant_id = {_SETTING})
        """,
    )


def downgrade() -> None:
    _exec(
        "DROP POLICY IF EXISTS login_log_select ON login_log",
        "DROP POLICY IF EXISTS login_log_update ON login_log",
        "DROP POLICY IF EXISTS login_log_delete ON login_log",
        "DROP POLICY IF EXISTS login_log_insert ON login_log",
        f"""
        CREATE POLICY login_log_tenant_isolation ON login_log
            USING (tenant_id = {_SETTING})
            WITH CHECK (tenant_id = {_SETTING})
        """,
    )
