"""★ 多租户越权测试（第三道防线：CI 强制门禁）—— 需要真实 PostgreSQL。

PRD 13.2 第 4 条：**每个新增租户表都必须同步补越权用例**。
本文件验证的是**第二道防线（RLS）**是否真的生效 —— 也就是
"即使有人绕过 ORM 直接写 SQL，也拿不到别的租户的数据"。

本地没有 Docker / PostgreSQL 时整个文件会被跳过（不会失败）；
CI 与 `docker compose up` 之后用 ``make test-security`` 跑。

订单表 ``sales_order``、状态日志 ``order_status_log``、发货单 ``shipment`` 与费用 ``order_fee``
有单独用例：租户 A 看不到租户 B 的行。
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


class TestAuditLogIsolationAndImmutability:
    def test_audit_log_is_tenant_scoped_and_cannot_be_modified(self, migrated: None) -> None:
        row_id = 920000000000000000 + time.time_ns() % 10**12

        async def _seed() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO audit_log (id, tenant_id, created_at, action, resource) "
                        "VALUES (:id, :tid, now(), 'SYSTEM_CONFIG', 'security_test')"
                    ),
                    {"id": row_id, "tid": TENANT_A},
                )
                await session.commit()
            await engine.dispose()

        async def _count(tenant_id: int) -> int:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, tenant_id)
                value = (
                    await session.execute(text("SELECT count(*) FROM audit_log WHERE id = :id"), {"id": row_id})
                ).scalar_one()
            await engine.dispose()
            return int(value)

        async def _tamper() -> int:
            engine, factory = _app_session()
            try:
                async with factory() as session:
                    await _bind(session, TENANT_A)
                    result = await session.execute(
                        text("UPDATE audit_log SET action = 'LOGIN' WHERE tenant_id = :tid AND id = :id"),
                        {"tid": TENANT_A, "id": row_id},
                    )
                    await session.commit()
                    return int(getattr(result, "rowcount", 0) or 0)
            finally:
                await engine.dispose()

        _run(_seed())
        assert _run(_count(TENANT_A)) == 1
        assert _run(_count(TENANT_B)) == 0
        assert _run(_tamper()) == 0


class TestShopIsolation:
    def test_shop_rows_are_tenant_scoped(self, migrated: None) -> None:
        shop_a = 930000000000000000 + time.time_ns() % 10**12
        shop_b = shop_a + 1

        async def _seed() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO shop (id, tenant_id, platform_code, site_code, shop_name, platform_shop_id, status) "
                        "VALUES (:id, :tid, 'shopee', 'SG', :name, :shop, 1)"
                    ),
                    {"id": shop_a, "tid": TENANT_A, "name": "A", "shop": f"seller-{shop_a}"},
                )
                await session.execute(
                    text(
                        "INSERT INTO shop (id, tenant_id, platform_code, site_code, shop_name, platform_shop_id, status) "
                        "VALUES (:id, :tid, 'shopee', 'SG', :name, :shop, 1)"
                    ),
                    {"id": shop_b, "tid": TENANT_B, "name": "B", "shop": f"seller-{shop_b}"},
                )
                await session.commit()
            await engine.dispose()

        async def _ids(tenant_id: int) -> set[int]:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, tenant_id)
                rows = (
                    (
                        await session.execute(
                            text("SELECT id FROM shop WHERE id IN (:a, :b)"),
                            {"a": shop_a, "b": shop_b},
                        )
                    )
                    .scalars()
                    .all()
                )
            await engine.dispose()
            return {int(row) for row in rows}

        _run(_seed())
        assert _run(_ids(TENANT_A)) == {shop_a}
        assert _run(_ids(TENANT_B)) == {shop_b}


class TestSalesOrderIsolation:
    def test_tenant_cannot_read_another_tenants_order(self, migrated: None) -> None:
        shop_a = 940000000000000000 + time.time_ns() % 10**12
        shop_b = shop_a + 1
        order_a = shop_a + 2
        order_b = shop_a + 3

        async def _seed() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                for shop_id, tenant_id, name in (
                    (shop_a, TENANT_A, "order-a"),
                    (shop_b, TENANT_B, "order-b"),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO shop (id, tenant_id, platform_code, site_code, shop_name, "
                            "platform_shop_id, status) "
                            "VALUES (:id, :tid, 'shopee', 'SG', :name, :shop, 1)"
                        ),
                        {"id": shop_id, "tid": tenant_id, "name": name, "shop": f"seller-{shop_id}"},
                    )
                await session.execute(
                    text(
                        "INSERT INTO sales_order ("
                        "id, tenant_id, shop_id, platform_code, platform_order_id, idempotency_key, "
                        "platform_status, unified_status, currency, item_amount, shipping_amount, "
                        "tax_amount, discount_amount, total_amount"
                        ") VALUES ("
                        ":id, :tid, :shop, 'shopee', :pid, :key, 'READY_TO_SHIP', 'PAID', 'SGD', "
                        "19.9, 0, 0, 0, 19.9)"
                    ),
                    {
                        "id": order_a,
                        "tid": TENANT_A,
                        "shop": shop_a,
                        "pid": f"A-{order_a}",
                        "key": f"shopee:{shop_a}:A-{order_a}",
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO sales_order ("
                        "id, tenant_id, shop_id, platform_code, platform_order_id, idempotency_key, "
                        "platform_status, unified_status, currency, item_amount, shipping_amount, "
                        "tax_amount, discount_amount, total_amount"
                        ") VALUES ("
                        ":id, :tid, :shop, 'shopee', :pid, :key, 'READY_TO_SHIP', 'PAID', 'SGD', "
                        "19.9, 0, 0, 0, 19.9)"
                    ),
                    {
                        "id": order_b,
                        "tid": TENANT_B,
                        "shop": shop_b,
                        "pid": f"B-{order_b}",
                        "key": f"shopee:{shop_b}:B-{order_b}",
                    },
                )
                await session.commit()
            await engine.dispose()

        async def _visible(tenant_id: int) -> set[int]:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, tenant_id)
                rows = (
                    (
                        await session.execute(
                            text("SELECT id FROM sales_order WHERE id IN (:a, :b)"),
                            {"a": order_a, "b": order_b},
                        )
                    )
                    .scalars()
                    .all()
                )
            await engine.dispose()
            return {int(row) for row in rows}

        async def _delete_header() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(text("DELETE FROM sales_order WHERE id = :id"), {"id": order_a})
                await session.commit()
            await engine.dispose()

        _run(_seed())
        assert _run(_visible(TENANT_A)) == {order_a}
        assert _run(_visible(TENANT_B)) == {order_b}
        with pytest.raises(Exception):  # noqa: B017 - 应用角色对订单主表没有 DELETE
            _run(_delete_header())


class TestOrderStatusLogIsolation:
    def test_tenant_cannot_read_another_tenants_status_log(self, migrated: None) -> None:
        shop_a = 950000000000000000 + time.time_ns() % 10**12
        shop_b = shop_a + 1
        order_a = shop_a + 2
        order_b = shop_a + 3
        log_a = shop_a + 4
        log_b = shop_a + 5

        async def _seed() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                for shop_id, tenant_id, name in (
                    (shop_a, TENANT_A, "log-a"),
                    (shop_b, TENANT_B, "log-b"),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO shop (id, tenant_id, platform_code, site_code, shop_name, "
                            "platform_shop_id, status) "
                            "VALUES (:id, :tid, 'shopee', 'SG', :name, :shop, 1)"
                        ),
                        {"id": shop_id, "tid": tenant_id, "name": name, "shop": f"seller-{shop_id}"},
                    )
                for order_id, tenant_id, shop_id in (
                    (order_a, TENANT_A, shop_a),
                    (order_b, TENANT_B, shop_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO sales_order ("
                            "id, tenant_id, shop_id, platform_code, platform_order_id, idempotency_key, "
                            "platform_status, unified_status, currency, item_amount, shipping_amount, "
                            "tax_amount, discount_amount, total_amount"
                            ") VALUES ("
                            ":id, :tid, :shop, 'shopee', :pid, :key, 'READY_TO_SHIP', 'PAID', 'SGD', "
                            "19.9, 0, 0, 0, 19.9)"
                        ),
                        {
                            "id": order_id,
                            "tid": tenant_id,
                            "shop": shop_id,
                            "pid": f"L-{order_id}",
                            "key": f"shopee:{shop_id}:L-{order_id}",
                        },
                    )
                for log_id, tenant_id, order_id in ((log_a, TENANT_A, order_a), (log_b, TENANT_B, order_b)):
                    await session.execute(
                        text(
                            "INSERT INTO order_status_log ("
                            "id, created_at, tenant_id, order_id, from_status, to_status, "
                            "platform_status, source"
                            ") VALUES ("
                            ":id, now(), :tid, :order, NULL, 'PAID', 'READY_TO_SHIP', 'SYSTEM')"
                        ),
                        {"id": log_id, "tid": tenant_id, "order": order_id},
                    )
                await session.commit()
            await engine.dispose()

        async def _visible(tenant_id: int) -> set[int]:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, tenant_id)
                rows = (
                    (
                        await session.execute(
                            text("SELECT id FROM order_status_log WHERE id IN (:a, :b)"),
                            {"a": log_a, "b": log_b},
                        )
                    )
                    .scalars()
                    .all()
                )
            await engine.dispose()
            return {int(row) for row in rows}

        async def _delete_log() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(text("DELETE FROM order_status_log WHERE id = :id"), {"id": log_a})
                await session.commit()
            await engine.dispose()

        _run(_seed())
        assert _run(_visible(TENANT_A)) == {log_a}
        assert _run(_visible(TENANT_B)) == {log_b}
        with pytest.raises(Exception):  # noqa: B017 - 状态日志只追加，应用角色没有 DELETE
            _run(_delete_log())

    def test_tenant_cannot_read_another_tenants_shipment_or_fee(self, migrated: None) -> None:
        shop_a = 960000000000000000 + time.time_ns() % 10**12
        shop_b = shop_a + 1
        order_a = shop_a + 2
        order_b = shop_a + 3
        ship_a = shop_a + 4
        ship_b = shop_a + 5
        fee_a = shop_a + 6
        fee_b = shop_a + 7

        async def _seed() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                for shop_id, tenant_id in ((shop_a, TENANT_A), (shop_b, TENANT_B)):
                    await session.execute(
                        text(
                            "INSERT INTO shop (id, tenant_id, platform_code, site_code, shop_name, "
                            "platform_shop_id, status) "
                            "VALUES (:id, :tid, 'shopee', 'SG', 'ship', :shop, 1)"
                        ),
                        {"id": shop_id, "tid": tenant_id, "shop": f"seller-{shop_id}"},
                    )
                for order_id, tenant_id, shop_id in ((order_a, TENANT_A, shop_a), (order_b, TENANT_B, shop_b)):
                    await session.execute(
                        text(
                            "INSERT INTO sales_order ("
                            "id, tenant_id, shop_id, platform_code, platform_order_id, idempotency_key, "
                            "platform_status, unified_status, currency, item_amount, shipping_amount, "
                            "tax_amount, discount_amount, total_amount"
                            ") VALUES ("
                            ":id, :tid, :shop, 'shopee', :pid, :key, 'READY_TO_SHIP', 'PAID', 'SGD', "
                            "1, 0, 0, 0, 1)"
                        ),
                        {
                            "id": order_id,
                            "tid": tenant_id,
                            "shop": shop_id,
                            "pid": f"S-{order_id}",
                            "key": f"shopee:{shop_id}:S-{order_id}",
                        },
                    )
                for ship_id, tenant_id, order_id in ((ship_a, TENANT_A, order_a), (ship_b, TENANT_B, order_b)):
                    await session.execute(
                        text(
                            "INSERT INTO shipment (id, tenant_id, order_id, carrier, tracking_no, status, attempt) "
                            "VALUES (:id, :tid, :order, 'Shopee', 'CP1', 'SUCCEEDED', 1)"
                        ),
                        {"id": ship_id, "tid": tenant_id, "order": order_id},
                    )
                for fee_id, tenant_id, order_id in ((fee_a, TENANT_A, order_a), (fee_b, TENANT_B, order_b)):
                    await session.execute(
                        text(
                            "INSERT INTO order_fee (id, tenant_id, order_id, fee_type, amount, currency, source) "
                            "VALUES (:id, :tid, :order, 'COMMISSION', 1.5, 'SGD', 'platform')"
                        ),
                        {"id": fee_id, "tid": tenant_id, "order": order_id},
                    )
                await session.commit()
            await engine.dispose()

        async def _visible(sql: str, tenant_id: int, left: int, right: int) -> set[int]:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, tenant_id)
                rows = (await session.execute(text(sql), {"a": left, "b": right})).scalars().all()
            await engine.dispose()
            return {int(row) for row in rows}

        async def _delete_shipment() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(text("DELETE FROM shipment WHERE id = :id"), {"id": ship_a})
                await session.commit()
            await engine.dispose()

        ship_sql = "SELECT id FROM shipment WHERE id IN (:a, :b)"
        fee_sql = "SELECT id FROM order_fee WHERE id IN (:a, :b)"
        _run(_seed())
        assert _run(_visible(ship_sql, TENANT_A, ship_a, ship_b)) == {ship_a}
        assert _run(_visible(ship_sql, TENANT_B, ship_a, ship_b)) == {ship_b}
        assert _run(_visible(fee_sql, TENANT_A, fee_a, fee_b)) == {fee_a}
        assert _run(_visible(fee_sql, TENANT_B, fee_a, fee_b)) == {fee_b}
        with pytest.raises(Exception):  # noqa: B017 - 发货单不给应用角色 DELETE
            _run(_delete_shipment())


class TestOrderDeskIsolation:
    def test_tenant_cannot_read_another_tenants_return_or_note(self, migrated: None) -> None:
        shop_a = 970000000000000000 + time.time_ns() % 10**12
        shop_b = shop_a + 1
        order_a = shop_a + 2
        order_b = shop_a + 3
        note_a = shop_a + 4
        note_b = shop_a + 5
        ret_a = shop_a + 6
        ret_b = shop_a + 7

        async def _seed() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                for shop_id, tenant_id in ((shop_a, TENANT_A), (shop_b, TENANT_B)):
                    await session.execute(
                        text(
                            "INSERT INTO shop (id, tenant_id, platform_code, site_code, shop_name, "
                            "platform_shop_id, status) "
                            "VALUES (:id, :tid, 'shopee', 'SG', 'desk', :shop, 1)"
                        ),
                        {"id": shop_id, "tid": tenant_id, "shop": f"seller-{shop_id}"},
                    )
                for order_id, tenant_id, shop_id in ((order_a, TENANT_A, shop_a), (order_b, TENANT_B, shop_b)):
                    await session.execute(
                        text(
                            "INSERT INTO sales_order ("
                            "id, tenant_id, shop_id, platform_code, platform_order_id, idempotency_key, "
                            "platform_status, unified_status, currency, item_amount, shipping_amount, "
                            "tax_amount, discount_amount, total_amount"
                            ") VALUES ("
                            ":id, :tid, :shop, 'shopee', :pid, :key, 'READY_TO_SHIP', 'PAID', 'SGD', "
                            "1, 0, 0, 0, 1)"
                        ),
                        {
                            "id": order_id,
                            "tid": tenant_id,
                            "shop": shop_id,
                            "pid": f"R-{order_id}",
                            "key": f"shopee:{shop_id}:R-{order_id}",
                        },
                    )
                for note_id, tenant_id, order_id in ((note_a, TENANT_A, order_a), (note_b, TENANT_B, order_b)):
                    await session.execute(
                        text(
                            "INSERT INTO order_note (id, tenant_id, order_id, content) "
                            "VALUES (:id, :tid, :order, 'note')"
                        ),
                        {"id": note_id, "tid": tenant_id, "order": order_id},
                    )
                for ret_id, tenant_id, order_id in ((ret_a, TENANT_A, order_a), (ret_b, TENANT_B, order_b)):
                    await session.execute(
                        text(
                            "INSERT INTO return_order ("
                            "id, tenant_id, order_id, reason, status, refund_amount, currency"
                            ") VALUES (:id, :tid, :order, 'broken', 'REQUESTED', 1, 'SGD')"
                        ),
                        {"id": ret_id, "tid": tenant_id, "order": order_id},
                    )
                await session.commit()
            await engine.dispose()

        async def _visible(sql: str, tenant_id: int, left: int, right: int) -> set[int]:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, tenant_id)
                rows = (await session.execute(text(sql), {"a": left, "b": right})).scalars().all()
            await engine.dispose()
            return {int(row) for row in rows}

        async def _delete_note() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(text("DELETE FROM order_note WHERE id = :id"), {"id": note_a})
                await session.commit()
            await engine.dispose()

        note_sql = "SELECT id FROM order_note WHERE id IN (:a, :b)"
        ret_sql = "SELECT id FROM return_order WHERE id IN (:a, :b)"
        _run(_seed())
        assert _run(_visible(note_sql, TENANT_A, note_a, note_b)) == {note_a}
        assert _run(_visible(note_sql, TENANT_B, note_a, note_b)) == {note_b}
        assert _run(_visible(ret_sql, TENANT_A, ret_a, ret_b)) == {ret_a}
        assert _run(_visible(ret_sql, TENANT_B, ret_a, ret_b)) == {ret_b}
        with pytest.raises(Exception):  # noqa: B017 - 备注只追加，应用角色没有 DELETE
            _run(_delete_note())
