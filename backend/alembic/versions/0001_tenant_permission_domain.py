"""租户与权限域 —— Alembic 迁移批次 1（PRD 6 章 / 9.2 ①）

创建 8 张表 + 多租户第二道防线（RLS 策略）+ 登录期跨租户成员查询函数。

## 本迁移包含的三件"非建表"工作（都是不可事后补救的）

1. **RLS 策略**：``crosspilot_app`` 这个运行时角色只能看到 ``tenant_id`` 等于
   当前会话变量的行。配合 ORM 自动注入构成两道防线。
   ⚠️ 刻意 **不使用 FORCE ROW LEVEL SECURITY**：表 owner（迁移账号）需要能绕过，
   否则 seed 脚本与后续迁移会被自己的策略挡住。
   安全性由「运行时账号 ≠ owner」保证，见 ``infra/postgres/init/01-init.sql``。

2. **``audit_log`` 分区**：按 RANGE(created_at) 月分区，**建表时就分**（PRD R12）。
   事后分区的代价是停服重建，所以这一步必须在第一个环境就做对。
   注意分区表的两个 PG 硬约束：主键必须含分区键；唯一约束必须含分区键。

3. **``app_user_tenants`` 函数（SECURITY DEFINER）**：登录时还没有租户上下文，
   RLS 会把 ``tenant_user`` 全挡掉。给应用角色开 ``BYPASSRLS`` 等于废掉第二道防线，
   所以用这个函数把"越权范围"收敛到数据库内部的一条固定查询上：
   它只能按 user_id 查成员关系，别的什么也做不了。

Revision ID: 0001_tenant_permission_domain
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_tenant_permission_domain"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 需要 RLS 保护的租户表（新增租户表时必须同步加进这里 + 补越权测试，PRD 13.2 第 4 条）
_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "tenant_user",
    "role",
    "user_data_scope",
    "member_invitation",
    "audit_log",
    "login_log",
)

_APP_ROLE = "crosspilot_app"

# 分区滚动窗口：当前月起，向前 1 个月（吞掉月初时序误差）+ 向后 12 个月
_PARTITION_MONTHS_BACK = 1
_PARTITION_MONTHS_FORWARD = 12


def _audit_columns() -> list[sa.Column]:
    """所有表共有的审计字段（PRD 9.1）。"""
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
    ]


def _add_months(base: date, months: int) -> date:
    month_index = base.month - 1 + months
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _exec_script(*statements: str) -> None:
    """逐条执行 SQL。

    ⚠️ 切记：**不要把多条语句拼成一次 ``op.execute()``**。
    asyncpg 是按 prepared statement 下发的，PostgreSQL 会直接拒绝：
        cannot insert multiple commands into a prepared statement
    容器模式用 psql 跑（simple query）看不出这个问题，只有本机 asyncpg 直连才会暴露。
    """
    for stmt in statements:
        op.execute(stmt)


def _tenant_policy_sql(table: str) -> list[str]:
    """生成租户隔离策略（**逐条返回**，见 ``_exec_script`` 的说明）。

    ``current_setting('app.current_tenant', true)`` 的第二个参数 ``missing_ok=true``
    很关键：未设置时返回 NULL 而不是抛错，于是 ``tenant_id = NULL`` 恒为 false
    ——**忘记绑定租户 = 一行都查不到**，这是刻意的 fail-closed。
    外层的 ``NULLIF(..., '')`` 是为了兼容"变量存在但为空串"的情况。
    """
    setting = "NULLIF(current_setting('app.current_tenant', true), '')::bigint"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id = {setting})
            WITH CHECK (tenant_id = {setting})
        """,
    ]


def _grant_app_privileges() -> None:
    """把运行时权限授予应用角色。

    用 DO 块包住：角色可能还没创建（例如先在本地跑迁移、后建角色），
    此时跳过而不是让迁移失败。角色的创建在 ``infra/postgres/init/01-init.sql``。
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                GRANT USAGE ON SCHEMA public TO {_APP_ROLE};
                GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {_APP_ROLE};
                GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE};
                GRANT EXECUTE ON FUNCTION app_user_tenants(bigint) TO {_APP_ROLE};
                ALTER DEFAULT PRIVILEGES IN SCHEMA public
                    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_APP_ROLE};
            ELSE
                RAISE NOTICE '角色 {_APP_ROLE} 不存在，跳过授权（本地开发常见，不影响迁移）';
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    # ==================================================================
    # 1) tenant —— 隔离的根，本身不带 tenant_id
    # ==================================================================
    op.create_table(
        "tenant",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, comment="租户唯一标识（登录/URL 用）"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="企业名称"),
        sa.Column("plan", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column("default_currency", sa.CHAR(length=3), nullable=False, comment="ISO 4217"),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("contact_email", sa.String(length=254), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_tenant"),
    )
    op.create_index("ix_tenant_created_at", "tenant", ["created_at"])
    op.create_index("ix_tenant_status", "tenant", ["status"])
    op.create_index("ix_tenant_status_plan", "tenant", ["status", "plan"])
    op.create_index("ix_tenant_code", "tenant", ["code"], unique=True)

    # ==================================================================
    # 2) sys_user —— 全局身份，不带 tenant_id
    # ==================================================================
    op.create_table(
        "sys_user",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("password_hash", sa.String(length=128), nullable=False, comment="bcrypt(SHA256(pwd))"),
        sa.Column("display_name", sa.String(length=64), nullable=True),
        sa.Column("avatar_url", sa.String(length=512), nullable=True),
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_login_count", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_sys_user"),
        sa.UniqueConstraint("phone", name="uq_sys_user_phone"),
    )
    op.create_index("ix_sys_user_email", "sys_user", ["email"], unique=True)
    op.create_index("ix_sys_user_created_at", "sys_user", ["created_at"])

    # ==================================================================
    # 3) tenant_user —— 租户成员关系
    # ==================================================================
    op.create_table(
        "tenant_user",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False, comment="租户 ID，隔离第一关键字"),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role_code", sa.String(length=32), nullable=False, comment="角色码，见 RoleCode"),
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column("nickname", sa.String(length=64), nullable=True, comment="租户内别名"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["sys_user.id"], name="fk_tenant_user_user_id_sys_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenant_user"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_user_tenant_id_user_id"),
    )
    op.create_index("ix_tenant_user_tenant_id", "tenant_user", ["tenant_id"])
    op.create_index("ix_tenant_user_deleted_at", "tenant_user", ["deleted_at"])
    op.create_index("ix_tenant_user_tenant_id_status", "tenant_user", ["tenant_id", "status"])
    op.create_index("ix_tenant_user_user_id", "tenant_user", ["user_id"])
    op.create_index("ix_tenant_user_created_at", "tenant_user", ["created_at"])

    # ==================================================================
    # 4) role —— 内置 8 角色按租户 seed（is_system=true，只读）
    # ==================================================================
    op.create_table(
        "role",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False, comment="租户 ID，隔离第一关键字"),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "is_system", sa.Boolean(), nullable=False, server_default=sa.text("false"), comment="系统内置角色，只读"
        ),
        sa.Column(
            "permission_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="权限点列表",
        ),
        sa.Column("description", sa.String(length=255), nullable=True),
        *_audit_columns(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_role"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_role_tenant_id_code"),
    )
    op.create_index("ix_role_tenant_id", "role", ["tenant_id"])
    op.create_index("ix_role_deleted_at", "role", ["deleted_at"])
    op.create_index("ix_role_tenant_id_is_system", "role", ["tenant_id", "is_system"])
    op.create_index("ix_role_created_at", "role", ["created_at"])

    # ==================================================================
    # 5) user_data_scope —— 成员数据范围（ALL / SELECTED / NONE）
    # ==================================================================
    op.create_table(
        "user_data_scope",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False, comment="租户 ID，隔离第一关键字"),
        sa.Column("tenant_user_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_type", sa.SmallInteger(), nullable=False, comment="见 ResourceType"),
        sa.Column("scope_type", sa.SmallInteger(), nullable=False),
        sa.Column(
            "shop_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
            server_default=sa.text("'{}'"),
            comment="scope_type=SELECTED 时的店铺白名单",
        ),
        *_audit_columns(),
        sa.ForeignKeyConstraint(
            ["tenant_user_id"],
            ["tenant_user.id"],
            name="fk_user_data_scope_tenant_user_id_tenant_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_data_scope"),
        sa.UniqueConstraint(
            "tenant_id", "tenant_user_id", "resource_type", name="uq_user_data_scope_tenant_id_tenant_user_id_resource_type"
        ),
    )
    op.create_index("ix_user_data_scope_tenant_id", "user_data_scope", ["tenant_id"])
    op.create_index(
        "ix_user_data_scope_tenant_id_tenant_user_id", "user_data_scope", ["tenant_id", "tenant_user_id"]
    )
    op.create_index("ix_user_data_scope_created_at", "user_data_scope", ["created_at"])

    # ==================================================================
    # 6) member_invitation —— 邀请令牌只存哈希
    # ==================================================================
    op.create_table(
        "member_invitation",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False, comment="租户 ID，隔离第一关键字"),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("role_code", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column("invited_by", sa.BigInteger(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id", name="pk_member_invitation"),
        sa.UniqueConstraint("token_hash", name="uq_member_invitation_token_hash"),
    )
    op.create_index("ix_member_invitation_tenant_id", "member_invitation", ["tenant_id"])
    op.create_index("ix_member_invitation_email", "member_invitation", ["email"])
    op.create_index("ix_member_invitation_expires_at", "member_invitation", ["expires_at"])
    op.create_index("ix_member_invitation_tenant_id_status", "member_invitation", ["tenant_id", "status"])
    op.create_index("ix_member_invitation_created_at", "member_invitation", ["created_at"])

    # ==================================================================
    # 7) audit_log —— **分区表**（建表即分区）
    # ==================================================================
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False, comment="租户 ID，隔离第一关键字"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False, comment="见 AuditAction"),
        sa.Column("resource", sa.String(length=64), nullable=False, comment="资源类型，如 shop/order"),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("ua", sa.String(length=512), nullable=True),
        sa.Column("request_id", sa.String(length=26), nullable=True, comment="trace_id，便于与应用日志对齐"),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # 分区表主键必须包含分区键
        sa.PrimaryKeyConstraint("id", "created_at", name="pk_audit_log"),
        postgresql_partition_by="RANGE (created_at)",
    )
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_tenant_id_created_at", "audit_log", ["tenant_id", "created_at"])
    op.create_index(
        "ix_audit_log_tenant_id_action_created_at", "audit_log", ["tenant_id", "action", "created_at"]
    )

    _create_audit_partitions()

    # ==================================================================
    # 8) login_log —— tenant_id 可为空（登录发生在租户上下文之前）
    # ==================================================================
    op.create_table(
        "login_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "tenant_id",
            sa.BigInteger(),
            nullable=True,
            comment="可为空：未知邮箱的登录尝试不归属任何租户",
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=True, comment="登录尝试的邮箱（便于排查爆破）"),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("ua", sa.String(length=512), nullable=True),
        sa.Column("result", sa.SmallInteger(), nullable=False),
        sa.Column("fail_reason", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_login_log"),
    )
    op.create_index("ix_login_log_tenant_id", "login_log", ["tenant_id"])
    op.create_index("ix_login_log_user_id", "login_log", ["user_id"])
    op.create_index("ix_login_log_tenant_id_created_at", "login_log", ["tenant_id", "created_at"])
    op.create_index("ix_login_log_email_created_at", "login_log", ["email", "created_at"])

    # ==================================================================
    # 9) 第二道防线：RLS
    # ==================================================================
    for table in _TENANT_SCOPED_TABLES:
        _exec_script(*_tenant_policy_sql(table))

    # tenant 表策略：只能看到/修改自己这一行。
    # INSERT 单独放开 —— 租户注册发生在任何租户上下文建立之前，
    # 且"创建一个新租户"本身不构成跨租户数据访问。
    _exec_script(
        "ALTER TABLE tenant ENABLE ROW LEVEL SECURITY",
        """
        CREATE POLICY tenant_select_self ON tenant
            FOR SELECT USING (id = NULLIF(current_setting('app.current_tenant', true), '')::bigint)
        """,
        """
        CREATE POLICY tenant_update_self ON tenant
            FOR UPDATE
            USING (id = NULLIF(current_setting('app.current_tenant', true), '')::bigint)
            WITH CHECK (id = NULLIF(current_setting('app.current_tenant', true), '')::bigint)
        """,
        """
        CREATE POLICY tenant_delete_self ON tenant
            FOR DELETE USING (id = NULLIF(current_setting('app.current_tenant', true), '')::bigint)
        """,
        "CREATE POLICY tenant_insert_registration ON tenant FOR INSERT WITH CHECK (true)",
    )

    # ==================================================================
    # 10) 登录期跨租户成员查询（SECURITY DEFINER）
    # ==================================================================
    _exec_script(
        """
        CREATE OR REPLACE FUNCTION app_user_tenants(p_user_id bigint)
        RETURNS TABLE (tenant_id bigint, code text, name text, role_code text, status smallint)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT tu.tenant_id, t.code::text, t.name::text, tu.role_code::text, tu.status::smallint
            FROM tenant_user tu
            JOIN tenant t ON t.id = tu.tenant_id
            WHERE tu.user_id = p_user_id
              AND tu.deleted_at IS NULL
            ORDER BY tu.created_at ASC;
        $$
        """,
        """
        COMMENT ON FUNCTION app_user_tenants(bigint) IS
            '登录阶段专用：在无租户上下文的情况下返回该用户的租户成员关系。'
            'SECURITY DEFINER 使其绕过 RLS，但作用范围被限制为"按 user_id 查成员关系"这一条固定查询。'
        """,
    )

    # ==================================================================
    # 11) 授予运行时角色权限
    # ==================================================================
    _grant_app_privileges()


def _create_audit_partitions() -> None:
    """创建 audit_log 的月度分区。

    ⚠️ 不建 DEFAULT 分区：DEFAULT 分区一旦写入数据，
    后续再想为那个月份建正式分区就必须搬数据（需要 ACCESS EXCLUSIVE 锁）。
    宁可让超范围写入直接报错（立刻暴露），也不要悄悄堆进 DEFAULT。

    因此有一个运维动作：**每月需提前创建下个月的分区**。
    已登记在开发规划 §13 上线前补做清单（批次 3：分区滚动维护任务）。
    """
    today = date.today().replace(day=1)
    start = _add_months(today, -_PARTITION_MONTHS_BACK)
    for i in range(_PARTITION_MONTHS_BACK + _PARTITION_MONTHS_FORWARD + 1):
        lower = _add_months(start, i)
        upper = _add_months(start, i + 1)
        op.execute(
            f"CREATE TABLE IF NOT EXISTS audit_log_{lower:%Y_%m} "
            f"PARTITION OF audit_log FOR VALUES FROM ('{lower.isoformat()}') TO ('{upper.isoformat()}')"
        )


def _drop_audit_partitions() -> None:
    today = date.today().replace(day=1)
    start = _add_months(today, -_PARTITION_MONTHS_BACK)
    for i in range(_PARTITION_MONTHS_BACK + _PARTITION_MONTHS_FORWARD + 1):
        lower = _add_months(start, i)
        op.execute(f"DROP TABLE IF EXISTS audit_log_{lower:%Y_%m}")


def downgrade() -> None:
    # 顺序：先函数，再分区，最后按外键依赖倒序删表
    op.execute("DROP FUNCTION IF EXISTS app_user_tenants(bigint)")
    _drop_audit_partitions()
    op.drop_table("login_log")
    op.drop_table("audit_log")
    op.drop_table("member_invitation")
    op.drop_table("user_data_scope")
    op.drop_table("role")
    op.drop_table("tenant_user")
    op.drop_table("sys_user")
    op.drop_table("tenant")
