-- =============================================================================
-- CrossPilot PostgreSQL 初始化（容器首次启动时执行一次）
--
-- 核心设计：**两个角色，权责分离**
--   crosspilot_owner —— 表 owner，跑 Alembic 迁移与种子脚本，绕过 RLS
--   crosspilot_app   —— 运行时账号，**不是表 owner**，受 RLS 约束
--
-- 多租户第二道防线（RLS）能生效的前提就是这条：
--   运行时账号不能是表 owner，也不能是 superuser，更不能有 BYPASSRLS。
--   只要其中有任意一条被破坏，RLS 会静默失效 —— 数据可以跨租户随便读，
--   而应用层完全感知不到。所以下面用 DO 块做了**运行时自检**。
-- =============================================================================

-- ---------------------------------------------------------------- 运行时角色
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crosspilot_app') THEN
        CREATE ROLE crosspilot_app
            LOGIN
            PASSWORD 'crosspilot_app_pwd'
            NOSUPERUSER      -- superuser 会绕过所有 RLS
            NOCREATEDB
            NOCREATEROLE
            NOBYPASSRLS;     -- ★ 绝不能给 BYPASSRLS，否则第二道防线直接消失
        RAISE NOTICE 'crosspilot_app 角色已创建';
    ELSE
        RAISE NOTICE 'crosspilot_app 角色已存在，跳过创建';
    END IF;
END
$$;

-- ---------------------------------------------------------------- 权限
GRANT CONNECT ON DATABASE crosspilot TO crosspilot_app;
GRANT USAGE ON SCHEMA public TO crosspilot_app;

-- 已存在的表（迁移已经跑过的情况下）
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO crosspilot_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO crosspilot_app;

-- 未来由 crosspilot_owner 新建的表/序列自动授权
-- （分区表每个月新建的审计分区就靠这条，否则每月都要手工授权）
ALTER DEFAULT PRIVILEGES FOR ROLE crosspilot_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO crosspilot_app;
ALTER DEFAULT PRIVILEGES FOR ROLE crosspilot_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO crosspilot_app;

-- ---------------------------------------------------------------- 自检
DO $$
DECLARE
    v_super boolean;
    v_bypass boolean;
BEGIN
    SELECT rolsuper, rolbypassrls INTO v_super, v_bypass
    FROM pg_roles WHERE rolname = 'crosspilot_app';

    IF v_super THEN
        RAISE EXCEPTION 'crosspilot_app 是 superuser —— RLS 会完全失效，拒绝启动';
    END IF;
    IF v_bypass THEN
        RAISE EXCEPTION 'crosspilot_app 拥有 BYPASSRLS —— 多租户隔离第二道防线失效，拒绝启动';
    END IF;
    RAISE NOTICE 'RLS 前置条件检查通过：crosspilot_app 既非 superuser 也无 BYPASSRLS';
END
$$;
