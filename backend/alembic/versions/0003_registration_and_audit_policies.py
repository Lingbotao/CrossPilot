"""注册安全函数与审计日志不可篡改策略。

Revision ID: 0003_identity_rbac_audit
Revises: 0002_login_log_insert_policy
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_identity_rbac_audit"
down_revision: str | None = "0002_login_log_insert_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "crosspilot_app"
_SETTING = "NULLIF(current_setting('app.current_tenant', true), '')::bigint"
_SIGNATURE = "app_register_tenant(bigint,bigint,bigint,text,text,text,text,text,text,text)"


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_register_tenant(
            p_tenant_id bigint,
            p_user_id bigint,
            p_membership_id bigint,
            p_email text,
            p_password_hash text,
            p_display_name text,
            p_tenant_code text,
            p_tenant_name text,
            p_default_currency text,
            p_timezone text
        )
        RETURNS TABLE (tenant_id bigint, user_id bigint, membership_id bigint)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        BEGIN
            INSERT INTO tenant (
                id, code, name, plan, status, default_currency, timezone,
                contact_email, created_at, updated_at
            ) VALUES (
                p_tenant_id, p_tenant_code, p_tenant_name, 1, 1,
                p_default_currency, p_timezone, p_email, now(), now()
            );

            INSERT INTO sys_user (
                id, email, password_hash, display_name, status,
                created_at, updated_at
            ) VALUES (
                p_user_id, lower(p_email), p_password_hash, p_display_name, 1,
                now(), now()
            );

            INSERT INTO tenant_user (
                id, tenant_id, user_id, role_code, status, joined_at,
                created_at, updated_at
            ) VALUES (
                p_membership_id, p_tenant_id, p_user_id, 'OWNER', 2, now(),
                now(), now()
            );

            RETURN QUERY SELECT p_tenant_id, p_user_id, p_membership_id;
        END;
        $$;
        """
    )
    op.execute(
        """
        COMMENT ON FUNCTION app_register_tenant(
            bigint,bigint,bigint,text,text,text,text,text,text,text
        ) IS
        '无租户上下文注册专用；仅原子创建 tenant、sys_user 与 OWNER membership。'
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crosspilot_app') THEN
                GRANT EXECUTE ON FUNCTION
                    app_register_tenant(bigint,bigint,bigint,text,text,text,text,text,text,text)
                    TO crosspilot_app;
            END IF;
        END
        $$;
        """
    )

    # 审计流水只允许当前租户读取和追加，任何运行时 UPDATE/DELETE 均由 RLS 拒绝。
    op.execute("DROP POLICY IF EXISTS audit_log_tenant_isolation ON audit_log")
    op.execute(f"CREATE POLICY audit_log_select ON audit_log FOR SELECT USING (tenant_id = {_SETTING})")
    op.execute(f"CREATE POLICY audit_log_insert ON audit_log FOR INSERT WITH CHECK (tenant_id = {_SETTING})")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS audit_log_insert ON audit_log")
    op.execute("DROP POLICY IF EXISTS audit_log_select ON audit_log")
    op.execute(
        f"""
        CREATE POLICY audit_log_tenant_isolation ON audit_log
            USING (tenant_id = {_SETTING})
            WITH CHECK (tenant_id = {_SETTING})
        """
    )
    op.execute(f"DROP FUNCTION IF EXISTS {_SIGNATURE}")
