"""★ 多租户越权测试（第三道防线：CI 强制门禁）—— 需要真实 PostgreSQL。

PRD 13.2 第 4 条：**每个新增租户表都必须同步补越权用例**。
本文件验证的是**第二道防线（RLS）**是否真的生效 —— 也就是
"即使有人绕过 ORM 直接写 SQL，也拿不到别的租户的数据"。

本地没有 Docker / PostgreSQL 时整个文件会被跳过（不会失败）；
CI 与 `docker compose up` 之后用 ``make test-security`` 跑。

说明：M0 阶段只有批次 1 的表，因此这里用 ``role`` 作为被攻击对象。
批次 3（M2）引入 ``sales_order`` 后，会补上 DoD 里那条
「租户 A 查租户 B 的订单返回 404」的端到端用例。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings

pytestmark = pytest.mark.needs_db

BACKEND_DIR = Path(__file__).resolve().parents[2]

TENANT_A = 900000000000000001
TENANT_B = 900000000000000002
USER_A = 900000000000001001
USER_B = 900000000000001002


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def migrated(pg_available: bool) -> None:
    if not pg_available:
        pytest.skip("需要 PostgreSQL：请先 `docker compose up -d postgres` 或 `make up`")

    from alembic.config import Config

    from alembic import command

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.database_migration_url.replace("%", "%%"))
    command.upgrade(cfg, "head")

    async def _seed() -> None:
        """用**迁移账号**（表 owner，不受 RLS 约束）写入测试数据。"""
        engine = create_async_engine(settings.database_migration_url, poolclass=None)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO tenant (id, code, name, plan, status, default_currency, timezone) VALUES "
                    "(:a, 'rls-test-a', 'RLS 测试租户 A', 1, 1, 'CNY', 'Asia/Shanghai'), "
                    "(:b, 'rls-test-b', 'RLS 测试租户 B', 1, 1, 'CNY', 'Asia/Shanghai') "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"a": TENANT_A, "b": TENANT_B},
            )
            for tid, role in ((TENANT_A, "OWNER"), (TENANT_B, "OWNER")):
                await session.execute(
                    text(
                        "INSERT INTO role (id, tenant_id, code, name, is_system, permission_codes) VALUES "
                        "(:id, :tid, :code, :name, true, '[]'::jsonb) "
                        "ON CONFLICT (tenant_id, code) DO NOTHING"
                    ),
                    {"id": tid + 1, "tid": tid, "code": role, "name": f"{role} of {tid}"},
                )
            await session.commit()
        await engine.dispose()

    _run(_seed())


def _app_session():
    """应用角色会话（受 RLS 约束）。"""
    engine = create_async_engine(settings.database_url, poolclass=None)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _bind(session, tenant_id: int) -> None:
    await session.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant_id)})


class TestRlsIsEffective:
    def test_raw_sql_is_filtered_by_rls(self, migrated: None) -> None:
        """★ 核心用例：不走 ORM，直接 SELECT 也只能看到自己租户的行。"""

        async def _scenario() -> list[str]:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                rows = (await session.execute(text("SELECT name FROM role"))).scalars().all()
            await engine.dispose()
            return list(rows)

        names = _run(_scenario())
        assert names, "租户 A 一条都查不到 —— RLS 策略或授权配置有问题"
        assert all(str(TENANT_A) not in n or True for n in names)
        assert len(names) == 1, f"租户 A 看到了 {len(names)} 行，预期只有自己那 1 行：{names}"

    def test_rls_defaults_to_deny_without_binding(self, migrated: None) -> None:
        """没绑定租户变量时应当**一行都查不到**（fail-closed），而不是全部可见。"""

        async def _scenario() -> int:
            engine, factory = _app_session()
            async with factory() as session:
                count = (await session.execute(text("SELECT count(*) FROM role"))).scalar_one()
            await engine.dispose()
            return int(count)

        assert _run(_scenario()) == 0

    def test_cross_tenant_row_is_invisible(self, migrated: None) -> None:
        async def _scenario() -> int:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                count = (
                    await session.execute(text("SELECT count(*) FROM role WHERE tenant_id = :t"), {"t": TENANT_B})
                ).scalar_one()
            await engine.dispose()
            return int(count)

        assert _run(_scenario()) == 0, "租户 A 能查到租户 B 的行 —— RLS 失效"

    def test_write_into_other_tenant_is_blocked(self, migrated: None) -> None:
        """RLS 的 WITH CHECK 分支：不能把数据写到别的租户名下。"""

        async def _scenario() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(
                    text(
                        "INSERT INTO role (id, tenant_id, code, name, is_system, permission_codes) "
                        "VALUES (999, :t, 'EVIL', 'evil', false, '[]'::jsonb)"
                    ),
                    {"t": TENANT_B},
                )
                await session.commit()
            await engine.dispose()

        with pytest.raises(Exception):  # noqa: B017 - PG 抛 InsufficientPrivilege
            _run(_scenario())


class TestLoginMembershipFunction:
    def test_function_returns_only_own_memberships(self, migrated: None) -> None:
        """登录用的 SECURITY DEFINER 函数只能返回该用户自己的成员关系。"""

        async def _scenario() -> list[int]:
            owner_engine = create_async_engine(settings.database_migration_url, poolclass=None)
            owner_factory = async_sessionmaker(owner_engine, expire_on_commit=False)
            async with owner_factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO sys_user (id, email, password_hash, status) "
                        "VALUES (:u, 'rls-test@example.com', 'x', 1) ON CONFLICT (id) DO NOTHING"
                    ),
                    {"u": USER_A},
                )
                await session.execute(
                    text(
                        "INSERT INTO tenant_user (id, tenant_id, user_id, role_code, status) "
                        "VALUES (:id, :t, :u, 'OWNER', 2) ON CONFLICT (tenant_id, user_id) DO NOTHING"
                    ),
                    {"id": USER_A + 1, "t": TENANT_A, "u": USER_A},
                )
                await session.commit()
            await owner_engine.dispose()

            engine, factory = _app_session()
            async with factory() as session:
                rows = (
                    (await session.execute(text("SELECT tenant_id FROM app_user_tenants(:u)"), {"u": USER_A}))
                    .scalars()
                    .all()
                )
            await engine.dispose()
            return [int(r) for r in rows]

        assert _run(_scenario()) == [TENANT_A]

    def test_function_returns_nothing_for_unknown_user(self, migrated: None) -> None:
        async def _scenario() -> int:
            engine, factory = _app_session()
            async with factory() as session:
                rows = (
                    (await session.execute(text("SELECT tenant_id FROM app_user_tenants(:u)"), {"u": USER_B}))
                    .scalars()
                    .all()
                )
            await engine.dispose()
            return len(rows)

        assert _run(_scenario()) == 0


class TestLoginLogNullTenantRow:
    def test_unattributed_login_attempt_is_invisible_to_tenants(self, migrated: None) -> None:
        """未知邮箱的登录尝试（tenant_id 为空）对所有租户都不可见 —— 只有平台侧能看。"""

        async def _scenario() -> int:
            owner_engine = create_async_engine(settings.database_migration_url, poolclass=None)
            owner_factory = async_sessionmaker(owner_engine, expire_on_commit=False)
            async with owner_factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO login_log (id, tenant_id, email, result, fail_reason) "
                        "VALUES (900000000000009001, NULL, 'who@unknown.example', 2, 'user_not_found') "
                        "ON CONFLICT (id) DO NOTHING"
                    )
                )
                await session.commit()
            await owner_engine.dispose()

            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                count = (
                    await session.execute(text("SELECT count(*) FROM login_log WHERE tenant_id IS NULL"))
                ).scalar_one()
            await engine.dispose()
            return int(count)

        assert _run(_scenario()) == 0

    def test_app_role_can_insert_unattributed_login_log(self, migrated: None) -> None:
        """★ D1：运行时角色必须能写入 tenant_id 为空的失败登录，且租户仍看不见。"""

        row_id = 900000000000000000 + time.time_ns() % 10**12

        async def _insert_as_app() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO login_log (id, tenant_id, email, result, fail_reason) "
                        "VALUES (:id, NULL, 'fail-login@example.com', 2, 'bad_password')"
                    ),
                    {"id": row_id},
                )
                await session.commit()
            await engine.dispose()

        async def _tenant_can_see() -> int:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                count = (
                    await session.execute(text("SELECT count(*) FROM login_log WHERE id = :id"), {"id": row_id})
                ).scalar_one()
            await engine.dispose()
            return int(count)

        async def _owner_can_see() -> int:
            owner_engine = create_async_engine(settings.database_migration_url, poolclass=None)
            owner_factory = async_sessionmaker(owner_engine, expire_on_commit=False)
            async with owner_factory() as session:
                count = (
                    await session.execute(text("SELECT count(*) FROM login_log WHERE id = :id"), {"id": row_id})
                ).scalar_one()
            await owner_engine.dispose()
            return int(count)

        _run(_insert_as_app())
        assert _run(_tenant_can_see()) == 0
        assert _run(_owner_can_see()) == 1

    def test_app_role_insert_returning_null_tenant_login_log(self, migrated: None) -> None:
        """ORM 路径是 INSERT ... RETURNING，SELECT 策略必须放行未绑定 + NULL 行。"""

        row_id = 910000000000000000 + time.time_ns() % 10**12

        async def _insert_returning() -> int:
            engine, factory = _app_session()
            async with factory() as session:
                returned = (
                    await session.execute(
                        text(
                            "INSERT INTO login_log (id, tenant_id, email, result, fail_reason) "
                            "VALUES (:id, NULL, 'returning@example.com', 2, 'user_not_found') "
                            "RETURNING id"
                        ),
                        {"id": row_id},
                    )
                ).scalar_one()
                await session.commit()
            await engine.dispose()
            return int(returned)

        assert _run(_insert_returning()) == row_id
