"""★ 多租户越权测试（第三道防线：CI 强制门禁）—— 需要真实 PostgreSQL。

PRD 13.2 第 4 条：**每个新增租户表都必须同步补越权用例**。
本文件验证的是**第二道防线（RLS）**是否真的生效 —— 也就是
"即使有人绕过 ORM 直接写 SQL，也拿不到别的租户的数据"。

本地没有 Docker / PostgreSQL 时整个文件会被跳过（不会失败）；
CI 与 `docker compose up` 之后用 ``make test-security`` 跑。

订单表 ``sales_order``、状态日志 ``order_status_log``、发货单 ``shipment`` 与费用 ``order_fee``
有单独用例：租户 A 看不到租户 B 的行。商品主数据 ``spu`` / ``sku`` 同样单独覆盖。
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


class TestProductMasterIsolation:
    def test_tenant_cannot_read_another_tenants_spu_or_sku(self, migrated: None) -> None:
        spu_a = 960000000000000000 + time.time_ns() % 10**12
        spu_b = spu_a + 1
        sku_a = spu_a + 2
        sku_b = spu_a + 3

        async def _seed() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                for spu_id, tenant_id, title in (
                    (spu_a, TENANT_A, "product-a"),
                    (spu_b, TENANT_B, "product-b"),
                ):
                    await session.execute(
                        text("INSERT INTO spu (id, tenant_id, title, status) VALUES (:id, :tid, :title, 'DRAFT')"),
                        {"id": spu_id, "tid": tenant_id, "title": title},
                    )
                for sku_id, tenant_id, spu_id, code in (
                    (sku_a, TENANT_A, spu_a, f"SKU-A-{sku_a}"),
                    (sku_b, TENANT_B, spu_b, f"SKU-B-{sku_b}"),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO sku ("
                            "id, tenant_id, spu_id, sku_code, weight_g, length_cm, width_cm, height_cm"
                            ") VALUES (:id, :tid, :spu, :code, 100, 10, 10, 10)"
                        ),
                        {"id": sku_id, "tid": tenant_id, "spu": spu_id, "code": code},
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

        async def _delete_spu() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(text("DELETE FROM spu WHERE id = :id"), {"id": spu_a})
                await session.commit()
            await engine.dispose()

        spu_sql = "SELECT id FROM spu WHERE id IN (:a, :b)"
        sku_sql = "SELECT id FROM sku WHERE id IN (:a, :b)"
        _run(_seed())
        assert _run(_visible(spu_sql, TENANT_A, spu_a, spu_b)) == {spu_a}
        assert _run(_visible(spu_sql, TENANT_B, spu_a, spu_b)) == {spu_b}
        assert _run(_visible(sku_sql, TENANT_A, sku_a, sku_b)) == {sku_a}
        assert _run(_visible(sku_sql, TENANT_B, sku_a, sku_b)) == {sku_b}
        with pytest.raises(Exception):  # noqa: B017 - 商品主数据只软删除，应用角色没有 DELETE
            _run(_delete_spu())


class TestListingMappingIsolation:
    def test_tenant_cannot_read_another_tenants_listing_or_category(self, migrated: None) -> None:
        base = 980000000000000000 + time.time_ns() % 10**12
        shop_a, shop_b = base, base + 1
        spu_a, spu_b = base + 2, base + 3
        sku_a, sku_b = base + 4, base + 5
        map_a, map_b = base + 6, base + 7
        listing_a, listing_b = base + 8, base + 9

        async def _seed() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                for shop_id, tenant_id in ((shop_a, TENANT_A), (shop_b, TENANT_B)):
                    await session.execute(
                        text(
                            "INSERT INTO shop (id, tenant_id, platform_code, site_code, shop_name, "
                            "platform_shop_id, status) "
                            "VALUES (:id, :tid, 'shopee', 'SG', 'listing', :shop, 1)"
                        ),
                        {"id": shop_id, "tid": tenant_id, "shop": f"seller-{shop_id}"},
                    )
                for spu_id, tenant_id in ((spu_a, TENANT_A), (spu_b, TENANT_B)):
                    await session.execute(
                        text("INSERT INTO spu (id, tenant_id, title, status) VALUES (:id, :tid, 'listing', 'DRAFT')"),
                        {"id": spu_id, "tid": tenant_id},
                    )
                for sku_id, tenant_id, spu_id in ((sku_a, TENANT_A, spu_a), (sku_b, TENANT_B, spu_b)):
                    await session.execute(
                        text(
                            "INSERT INTO sku ("
                            "id, tenant_id, spu_id, sku_code, weight_g, length_cm, width_cm, height_cm"
                            ") VALUES (:id, :tid, :spu, :code, 100, 10, 10, 10)"
                        ),
                        {"id": sku_id, "tid": tenant_id, "spu": spu_id, "code": f"MAP-{sku_id}"},
                    )
                for map_id, tenant_id, code in (
                    (map_a, TENANT_A, f"tee-a-{map_a}"),
                    (map_b, TENANT_B, f"tee-b-{map_b}"),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO category_mapping ("
                            "id, tenant_id, platform_code, site_code, platform_category_id, "
                            "local_category_code, name"
                            ") VALUES (:id, :tid, 'shopee', 'SG', '1001', :code, 'T恤')"
                        ),
                        {"id": map_id, "tid": tenant_id, "code": code},
                    )
                for listing_id, tenant_id, sku_id, shop_id in (
                    (listing_a, TENANT_A, sku_a, shop_a),
                    (listing_b, TENANT_B, sku_b, shop_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO listing (id, tenant_id, sku_id, shop_id, status) "
                            "VALUES (:id, :tid, :sku, :shop, 'DRAFT')"
                        ),
                        {"id": listing_id, "tid": tenant_id, "sku": sku_id, "shop": shop_id},
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

        async def _delete_listing() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(text("DELETE FROM listing WHERE id = :id"), {"id": listing_a})
                await session.commit()
            await engine.dispose()

        listing_sql = "SELECT id FROM listing WHERE id IN (:a, :b)"
        mapping_sql = "SELECT id FROM category_mapping WHERE id IN (:a, :b)"
        _run(_seed())
        assert _run(_visible(listing_sql, TENANT_A, listing_a, listing_b)) == {listing_a}
        assert _run(_visible(listing_sql, TENANT_B, listing_a, listing_b)) == {listing_b}
        assert _run(_visible(mapping_sql, TENANT_A, map_a, map_b)) == {map_a}
        assert _run(_visible(mapping_sql, TENANT_B, map_a, map_b)) == {map_b}
        with pytest.raises(Exception):  # noqa: B017 - Listing 是主数据，应用角色没有 DELETE
            _run(_delete_listing())


class TestListingBatchIsolation:
    def test_tenant_cannot_read_another_tenants_listing_batch(self, migrated: None) -> None:
        base = 981000000000000000 + time.time_ns() % 10**12
        shop_a, shop_b = base, base + 1
        spu_a, spu_b = base + 2, base + 3
        sku_a, sku_b = base + 4, base + 5
        batch_a, batch_b = base + 6, base + 7
        item_a, item_b = base + 8, base + 9

        async def _seed() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                for shop_id, tenant_id in ((shop_a, TENANT_A), (shop_b, TENANT_B)):
                    await session.execute(
                        text(
                            "INSERT INTO shop (id, tenant_id, platform_code, site_code, shop_name, "
                            "platform_shop_id, status) "
                            "VALUES (:id, :tid, 'shopee', 'SG', 'batch', :shop, 1)"
                        ),
                        {"id": shop_id, "tid": tenant_id, "shop": f"batch-{shop_id}"},
                    )
                for spu_id, tenant_id in ((spu_a, TENANT_A), (spu_b, TENANT_B)):
                    await session.execute(
                        text("INSERT INTO spu (id, tenant_id, title, status) VALUES (:id, :tid, 'batch', 'DRAFT')"),
                        {"id": spu_id, "tid": tenant_id},
                    )
                for sku_id, tenant_id, spu_id in ((sku_a, TENANT_A, spu_a), (sku_b, TENANT_B, spu_b)):
                    await session.execute(
                        text(
                            "INSERT INTO sku ("
                            "id, tenant_id, spu_id, sku_code, weight_g, length_cm, width_cm, height_cm"
                            ") VALUES (:id, :tid, :spu, :code, 100, 10, 10, 10)"
                        ),
                        {"id": sku_id, "tid": tenant_id, "spu": spu_id, "code": f"BATCH-{sku_id}"},
                    )
                for batch_id, tenant_id in ((batch_a, TENANT_A), (batch_b, TENANT_B)):
                    await session.execute(
                        text(
                            "INSERT INTO listing_batch (id, tenant_id, kind, status, total, succeeded, failed, skipped) "
                            "VALUES (:id, :tid, 'PUBLISH', 'PENDING', 1, 0, 0, 0)"
                        ),
                        {"id": batch_id, "tid": tenant_id},
                    )
                for item_id, tenant_id, batch_id, sku_id, shop_id in (
                    (item_a, TENANT_A, batch_a, sku_a, shop_a),
                    (item_b, TENANT_B, batch_b, sku_b, shop_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO listing_batch_item (id, tenant_id, batch_id, sku_id, shop_id, status) "
                            "VALUES (:id, :tid, :batch, :sku, :shop, 'PENDING')"
                        ),
                        {"id": item_id, "tid": tenant_id, "batch": batch_id, "sku": sku_id, "shop": shop_id},
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

        async def _delete_batch() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(text("DELETE FROM listing_batch WHERE id = :id"), {"id": batch_a})
                await session.commit()
            await engine.dispose()

        batch_sql = "SELECT id FROM listing_batch WHERE id IN (:a, :b)"
        item_sql = "SELECT id FROM listing_batch_item WHERE id IN (:a, :b)"
        _run(_seed())
        assert _run(_visible(batch_sql, TENANT_A, batch_a, batch_b)) == {batch_a}
        assert _run(_visible(batch_sql, TENANT_B, batch_a, batch_b)) == {batch_b}
        assert _run(_visible(item_sql, TENANT_A, item_a, item_b)) == {item_a}
        assert _run(_visible(item_sql, TENANT_B, item_a, item_b)) == {item_b}
        with pytest.raises(Exception):  # noqa: B017 - 批次是操作流水，应用角色没有 DELETE
            _run(_delete_batch())


class TestCatalogMediaIsolation:
    def test_tenant_cannot_read_another_tenants_image_or_listing_diff(self, migrated: None) -> None:
        base = 982000000000000000 + time.time_ns() % 10**12
        shop_a, shop_b = base, base + 1
        spu_a, spu_b = base + 2, base + 3
        sku_a, sku_b = base + 4, base + 5
        listing_a, listing_b = base + 6, base + 7
        image_a, image_b = base + 8, base + 9
        diff_a, diff_b = base + 10, base + 11

        async def _seed() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                for shop_id, tenant_id in ((shop_a, TENANT_A), (shop_b, TENANT_B)):
                    await session.execute(
                        text(
                            "INSERT INTO shop (id, tenant_id, platform_code, site_code, shop_name, "
                            "platform_shop_id, status) "
                            "VALUES (:id, :tid, 'shopee', 'SG', 'media', :shop, 1)"
                        ),
                        {"id": shop_id, "tid": tenant_id, "shop": f"media-{shop_id}"},
                    )
                for spu_id, tenant_id in ((spu_a, TENANT_A), (spu_b, TENANT_B)):
                    await session.execute(
                        text("INSERT INTO spu (id, tenant_id, title, status) VALUES (:id, :tid, 'media', 'DRAFT')"),
                        {"id": spu_id, "tid": tenant_id},
                    )
                for sku_id, tenant_id, spu_id in ((sku_a, TENANT_A, spu_a), (sku_b, TENANT_B, spu_b)):
                    await session.execute(
                        text(
                            "INSERT INTO sku ("
                            "id, tenant_id, spu_id, sku_code, weight_g, length_cm, width_cm, height_cm"
                            ") VALUES (:id, :tid, :spu, :code, 100, 10, 10, 10)"
                        ),
                        {"id": sku_id, "tid": tenant_id, "spu": spu_id, "code": f"MEDIA-{sku_id}"},
                    )
                for listing_id, tenant_id, sku_id, shop_id in (
                    (listing_a, TENANT_A, sku_a, shop_a),
                    (listing_b, TENANT_B, sku_b, shop_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO listing (id, tenant_id, sku_id, shop_id, status) "
                            "VALUES (:id, :tid, :sku, :shop, 'DRAFT')"
                        ),
                        {"id": listing_id, "tid": tenant_id, "sku": sku_id, "shop": shop_id},
                    )
                for image_id, tenant_id, spu_id in ((image_a, TENANT_A, spu_a), (image_b, TENANT_B, spu_b)):
                    await session.execute(
                        text(
                            "INSERT INTO product_image ("
                            "id, tenant_id, spu_id, object_key, image_type, sort, width_px, height_px, "
                            "byte_size, content_type, white_background, platform_compliance"
                            ") VALUES (:id, :tid, :spu, :key, 'MAIN', 0, 1000, 1000, 12, 'image/png', true, '[]'::jsonb)"
                        ),
                        {"id": image_id, "tid": tenant_id, "spu": spu_id, "key": f"img-{image_id}"},
                    )
                for diff_id, tenant_id, listing_id, shop_id in (
                    (diff_a, TENANT_A, listing_a, shop_a),
                    (diff_b, TENANT_B, listing_b, shop_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO listing_diff ("
                            "id, tenant_id, listing_id, shop_id, field_name, local_value, remote_value, status"
                            ") VALUES (:id, :tid, :listing, :shop, 'price', '10.000000', '15.000000', 'PENDING')"
                        ),
                        {"id": diff_id, "tid": tenant_id, "listing": listing_id, "shop": shop_id},
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

        async def _delete_image() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(text("DELETE FROM product_image WHERE id = :id"), {"id": image_a})
                await session.commit()
            await engine.dispose()

        async def _delete_diff() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(text("DELETE FROM listing_diff WHERE id = :id"), {"id": diff_a})
                await session.commit()
            await engine.dispose()

        image_sql = "SELECT id FROM product_image WHERE id IN (:a, :b)"
        diff_sql = "SELECT id FROM listing_diff WHERE id IN (:a, :b)"
        _run(_seed())
        assert _run(_visible(image_sql, TENANT_A, image_a, image_b)) == {image_a}
        assert _run(_visible(image_sql, TENANT_B, image_a, image_b)) == {image_b}
        assert _run(_visible(diff_sql, TENANT_A, diff_a, diff_b)) == {diff_a}
        assert _run(_visible(diff_sql, TENANT_B, diff_a, diff_b)) == {diff_b}
        with pytest.raises(Exception):  # noqa: B017 - 图片是主数据，应用角色没有 DELETE
            _run(_delete_image())
        with pytest.raises(Exception):  # noqa: B017 - 差异清单是流水，应用角色没有 DELETE
            _run(_delete_diff())


class TestLocaleIsolation:
    def test_tenant_cannot_read_another_tenants_copy_glossary_or_sensitive_term(self, migrated: None) -> None:
        base = 983000000000000000 + time.time_ns() % 10**12
        shop_a, shop_b = base, base + 1
        spu_a, spu_b = base + 2, base + 3
        sku_a, sku_b = base + 4, base + 5
        listing_a, listing_b = base + 6, base + 7
        content_a, content_b = base + 8, base + 9
        glossary_a, glossary_b = base + 10, base + 11
        sensitive_a, sensitive_b = base + 12, base + 13

        async def _seed() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                for shop_id, tenant_id in ((shop_a, TENANT_A), (shop_b, TENANT_B)):
                    await session.execute(
                        text(
                            "INSERT INTO shop (id, tenant_id, platform_code, site_code, shop_name, "
                            "platform_shop_id, status) "
                            "VALUES (:id, :tid, 'shopee', 'SG', 'locale', :shop, 1)"
                        ),
                        {"id": shop_id, "tid": tenant_id, "shop": f"locale-{shop_id}"},
                    )
                for spu_id, tenant_id in ((spu_a, TENANT_A), (spu_b, TENANT_B)):
                    await session.execute(
                        text("INSERT INTO spu (id, tenant_id, title, status) VALUES (:id, :tid, 'locale', 'DRAFT')"),
                        {"id": spu_id, "tid": tenant_id},
                    )
                for sku_id, tenant_id, spu_id in ((sku_a, TENANT_A, spu_a), (sku_b, TENANT_B, spu_b)):
                    await session.execute(
                        text(
                            "INSERT INTO sku ("
                            "id, tenant_id, spu_id, sku_code, weight_g, length_cm, width_cm, height_cm"
                            ") VALUES (:id, :tid, :spu, :code, 100, 10, 10, 10)"
                        ),
                        {"id": sku_id, "tid": tenant_id, "spu": spu_id, "code": f"LOCALE-{sku_id}"},
                    )
                for listing_id, tenant_id, sku_id, shop_id in (
                    (listing_a, TENANT_A, sku_a, shop_a),
                    (listing_b, TENANT_B, sku_b, shop_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO listing (id, tenant_id, sku_id, shop_id, status) "
                            "VALUES (:id, :tid, :sku, :shop, 'DRAFT')"
                        ),
                        {"id": listing_id, "tid": tenant_id, "sku": sku_id, "shop": shop_id},
                    )
                for content_id, tenant_id, listing_id in (
                    (content_a, TENANT_A, listing_a),
                    (content_b, TENANT_B, listing_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO listing_content ("
                            "id, tenant_id, listing_id, lang, title, description, bullet_points, quality_status"
                            ") VALUES (:id, :tid, :listing, 'en', 'Cup', '', '[]'::jsonb, 'MT_DRAFT')"
                        ),
                        {"id": content_id, "tid": tenant_id, "listing": listing_id},
                    )
                for term_id, tenant_id in ((glossary_a, TENANT_A), (glossary_b, TENANT_B)):
                    await session.execute(
                        text(
                            "INSERT INTO glossary_term ("
                            "id, tenant_id, source_lang, source_term, target_lang, target_term"
                            ") VALUES (:id, :tid, 'zh-CN', :term, 'en', 'Brand')"
                        ),
                        {"id": term_id, "tid": tenant_id, "term": f"品牌{term_id}"},
                    )
                for term_id, tenant_id in ((sensitive_a, TENANT_A), (sensitive_b, TENANT_B)):
                    await session.execute(
                        text(
                            "INSERT INTO sensitive_term ("
                            "id, tenant_id, market, lang, keyword, suggest_replacement"
                            ") VALUES (:id, :tid, 'SG', 'en', :word, 'care')"
                        ),
                        {"id": term_id, "tid": tenant_id, "word": f"cure{term_id}"},
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

        async def _delete_content() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(text("DELETE FROM listing_content WHERE id = :id"), {"id": content_a})
                await session.commit()
            await engine.dispose()

        content_sql = "SELECT id FROM listing_content WHERE id IN (:a, :b)"
        glossary_sql = "SELECT id FROM glossary_term WHERE id IN (:a, :b)"
        sensitive_sql = "SELECT id FROM sensitive_term WHERE id IN (:a, :b)"
        _run(_seed())
        assert _run(_visible(content_sql, TENANT_A, content_a, content_b)) == {content_a}
        assert _run(_visible(content_sql, TENANT_B, content_a, content_b)) == {content_b}
        assert _run(_visible(glossary_sql, TENANT_A, glossary_a, glossary_b)) == {glossary_a}
        assert _run(_visible(glossary_sql, TENANT_B, glossary_a, glossary_b)) == {glossary_b}
        assert _run(_visible(sensitive_sql, TENANT_A, sensitive_a, sensitive_b)) == {sensitive_a}
        assert _run(_visible(sensitive_sql, TENANT_B, sensitive_a, sensitive_b)) == {sensitive_b}
        with pytest.raises(Exception):  # noqa: B017 - 文案是主数据，应用角色没有 DELETE
            _run(_delete_content())


class TestInventoryIsolation:
    def test_tenant_cannot_read_another_tenants_stock_and_ledgers_reject_delete(self, migrated: None) -> None:
        base = 984000000000000000 + time.time_ns() % 10**12
        shop_a, shop_b = base, base + 1
        spu_a, spu_b = base + 2, base + 3
        sku_a, sku_b = base + 4, base + 5
        order_a, order_b = base + 6, base + 7
        warehouse_a, warehouse_b = base + 8, base + 9
        inventory_a, inventory_b = base + 10, base + 11
        flow_a, flow_b = base + 12, base + 13
        hold_a, hold_b = base + 14, base + 15
        safety_a, safety_b = base + 16, base + 17
        push_a, push_b = base + 18, base + 19
        warehouse2_a, warehouse2_b = base + 40, base + 41
        transfer_a, transfer_b = base + 42, base + 43
        transfer_line_a, transfer_line_b = base + 44, base + 45
        taking_a, taking_b = base + 46, base + 47
        taking_line_a, taking_line_b = base + 48, base + 49

        async def _seed() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                for shop_id, tenant_id in ((shop_a, TENANT_A), (shop_b, TENANT_B)):
                    await session.execute(
                        text(
                            "INSERT INTO shop (id, tenant_id, platform_code, site_code, shop_name, "
                            "platform_shop_id, status) "
                            "VALUES (:id, :tid, 'shopee', 'SG', 'stock', :shop, 1)"
                        ),
                        {"id": shop_id, "tid": tenant_id, "shop": f"stock-{shop_id}"},
                    )
                for spu_id, tenant_id in ((spu_a, TENANT_A), (spu_b, TENANT_B)):
                    await session.execute(
                        text("INSERT INTO spu (id, tenant_id, title, status) VALUES (:id, :tid, 'stock', 'DRAFT')"),
                        {"id": spu_id, "tid": tenant_id},
                    )
                for sku_id, tenant_id, spu_id in ((sku_a, TENANT_A, spu_a), (sku_b, TENANT_B, spu_b)):
                    await session.execute(
                        text(
                            "INSERT INTO sku ("
                            "id, tenant_id, spu_id, sku_code, weight_g, length_cm, width_cm, height_cm"
                            ") VALUES (:id, :tid, :spu, :code, 100, 10, 10, 10)"
                        ),
                        {"id": sku_id, "tid": tenant_id, "spu": spu_id, "code": f"STK-{sku_id}"},
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
                            "pid": f"P-{order_id}",
                            "key": f"shopee:{shop_id}:P-{order_id}",
                        },
                    )
                for warehouse_id, tenant_id in ((warehouse_a, TENANT_A), (warehouse_b, TENANT_B)):
                    await session.execute(
                        text(
                            "INSERT INTO warehouse (id, tenant_id, name, warehouse_type, country) "
                            "VALUES (:id, :tid, '主仓', 'LOCAL', 'CN')"
                        ),
                        {"id": warehouse_id, "tid": tenant_id},
                    )
                for inventory_id, tenant_id, sku_id, warehouse_id in (
                    (inventory_a, TENANT_A, sku_a, warehouse_a),
                    (inventory_b, TENANT_B, sku_b, warehouse_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO inventory (id, tenant_id, sku_id, warehouse_id, available) "
                            "VALUES (:id, :tid, :sku, :warehouse, 3)"
                        ),
                        {"id": inventory_id, "tid": tenant_id, "sku": sku_id, "warehouse": warehouse_id},
                    )
                for flow_id, tenant_id, inventory_id, sku_id, warehouse_id in (
                    (flow_a, TENANT_A, inventory_a, sku_a, warehouse_a),
                    (flow_b, TENANT_B, inventory_b, sku_b, warehouse_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO inventory_flow ("
                            "id, tenant_id, inventory_id, sku_id, warehouse_id, flow_type, quantity, "
                            "before_qty, after_qty"
                            ") VALUES (:id, :tid, :inventory, :sku, :warehouse, 'INBOUND', 3, 0, 3)"
                        ),
                        {
                            "id": flow_id,
                            "tid": tenant_id,
                            "inventory": inventory_id,
                            "sku": sku_id,
                            "warehouse": warehouse_id,
                        },
                    )
                for hold_id, tenant_id, order_id, sku_id in (
                    (hold_a, TENANT_A, order_a, sku_a),
                    (hold_b, TENANT_B, order_b, sku_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO inventory_hold ("
                            "id, tenant_id, order_id, order_item_id, sku_id, status"
                            ") VALUES (:id, :tid, :order_id, :item, :sku, 'SHORT')"
                        ),
                        {"id": hold_id, "tid": tenant_id, "order_id": order_id, "item": hold_id, "sku": sku_id},
                    )
                for safety_id, tenant_id, sku_id in ((safety_a, TENANT_A, sku_a), (safety_b, TENANT_B, sku_b)):
                    await session.execute(
                        text(
                            "INSERT INTO platform_safety_stock ("
                            "id, tenant_id, sku_id, platform_code, lead_time_days, cover_days"
                            ") VALUES (:id, :tid, :sku, 'shopee', 7, 7)"
                        ),
                        {"id": safety_id, "tid": tenant_id, "sku": sku_id},
                    )
                for warehouse_id, tenant_id in ((warehouse2_a, TENANT_A), (warehouse2_b, TENANT_B)):
                    await session.execute(
                        text(
                            "INSERT INTO warehouse (id, tenant_id, name, warehouse_type, country) "
                            "VALUES (:id, :tid, '次仓', 'OVERSEAS', 'SG')"
                        ),
                        {"id": warehouse_id, "tid": tenant_id},
                    )
                for transfer_id, tenant_id, from_id, to_id in (
                    (transfer_a, TENANT_A, warehouse_a, warehouse2_a),
                    (transfer_b, TENANT_B, warehouse_b, warehouse2_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO stock_transfer ("
                            "id, tenant_id, from_warehouse_id, to_warehouse_id, status"
                            ") VALUES (:id, :tid, :src, :dst, 'DRAFT')"
                        ),
                        {"id": transfer_id, "tid": tenant_id, "src": from_id, "dst": to_id},
                    )
                for line_id, tenant_id, transfer_id, sku_id in (
                    (transfer_line_a, TENANT_A, transfer_a, sku_a),
                    (transfer_line_b, TENANT_B, transfer_b, sku_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO stock_transfer_line (id, tenant_id, transfer_id, sku_id, quantity) "
                            "VALUES (:id, :tid, :transfer, :sku, 1)"
                        ),
                        {"id": line_id, "tid": tenant_id, "transfer": transfer_id, "sku": sku_id},
                    )
                for taking_id, tenant_id, warehouse_id in (
                    (taking_a, TENANT_A, warehouse_a),
                    (taking_b, TENANT_B, warehouse_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO stock_taking (id, tenant_id, warehouse_id, status) "
                            "VALUES (:id, :tid, :warehouse, 'DRAFT')"
                        ),
                        {"id": taking_id, "tid": tenant_id, "warehouse": warehouse_id},
                    )
                for line_id, tenant_id, taking_id, sku_id in (
                    (taking_line_a, TENANT_A, taking_a, sku_a),
                    (taking_line_b, TENANT_B, taking_b, sku_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO stock_taking_line (id, tenant_id, taking_id, sku_id, book_qty) "
                            "VALUES (:id, :tid, :taking, :sku, 3)"
                        ),
                        {"id": line_id, "tid": tenant_id, "taking": taking_id, "sku": sku_id},
                    )
                for push_id, tenant_id, shop_id, sku_id in (
                    (push_a, TENANT_A, shop_a, sku_a),
                    (push_b, TENANT_B, shop_b, sku_b),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO inventory_push_log ("
                            "id, tenant_id, shop_id, sku_id, platform_code, quantity, status"
                            ") VALUES (:id, :tid, :shop, :sku, 'shopee', 1, 'SUCCESS')"
                        ),
                        {"id": push_id, "tid": tenant_id, "shop": shop_id, "sku": sku_id},
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

        async def _delete_flow() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(text("DELETE FROM inventory_flow WHERE id = :id"), {"id": flow_a})
                await session.commit()
            await engine.dispose()

        async def _delete_push() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(text("DELETE FROM inventory_push_log WHERE id = :id"), {"id": push_a})
                await session.commit()
            await engine.dispose()

        async def _reject_negative() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                extra_warehouse = base + 31
                await session.execute(
                    text(
                        "INSERT INTO warehouse (id, tenant_id, name, warehouse_type, country) "
                        "VALUES (:id, :tid, '次仓', 'LOCAL', 'CN')"
                    ),
                    {"id": extra_warehouse, "tid": TENANT_A},
                )
                await session.execute(
                    text(
                        "INSERT INTO inventory (id, tenant_id, sku_id, warehouse_id, available, occupied) "
                        "VALUES (:id, :tid, :sku, :warehouse, 1, 2)"
                    ),
                    {
                        "id": base + 30,
                        "tid": TENANT_A,
                        "sku": sku_a,
                        "warehouse": extra_warehouse,
                    },
                )
                await session.commit()
            await engine.dispose()

        async def _stop_at_available() -> int:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                applied = 0
                for _ in range(5):
                    result = await session.execute(
                        text(
                            "UPDATE inventory SET occupied = occupied + 1, version = version + 1 "
                            "WHERE id = :id AND available - occupied >= 1"
                        ),
                        {"id": inventory_a},
                    )
                    if result.rowcount == 1:
                        applied += 1
                occupied = (
                    await session.execute(text("SELECT occupied FROM inventory WHERE id = :id"), {"id": inventory_a})
                ).scalar_one()
                await session.commit()
            await engine.dispose()
            assert int(occupied) == 3
            return applied

        _run(_seed())
        pairs = (
            ("SELECT id FROM warehouse WHERE id IN (:a, :b)", warehouse_a, warehouse_b),
            ("SELECT id FROM inventory WHERE id IN (:a, :b)", inventory_a, inventory_b),
            ("SELECT id FROM inventory_flow WHERE id IN (:a, :b)", flow_a, flow_b),
            ("SELECT id FROM inventory_hold WHERE id IN (:a, :b)", hold_a, hold_b),
            ("SELECT id FROM platform_safety_stock WHERE id IN (:a, :b)", safety_a, safety_b),
            ("SELECT id FROM inventory_push_log WHERE id IN (:a, :b)", push_a, push_b),
            ("SELECT id FROM stock_transfer WHERE id IN (:a, :b)", transfer_a, transfer_b),
            ("SELECT id FROM stock_transfer_line WHERE id IN (:a, :b)", transfer_line_a, transfer_line_b),
            ("SELECT id FROM stock_taking WHERE id IN (:a, :b)", taking_a, taking_b),
            ("SELECT id FROM stock_taking_line WHERE id IN (:a, :b)", taking_line_a, taking_line_b),
        )
        for sql, left, right in pairs:
            assert _run(_visible(sql, TENANT_A, left, right)) == {left}
            assert _run(_visible(sql, TENANT_B, left, right)) == {right}
        with pytest.raises(Exception):  # noqa: B017 - 流水不能删
            _run(_delete_flow())
        with pytest.raises(Exception):  # noqa: B017 - 回传日志不能删
            _run(_delete_push())
        with pytest.raises(Exception):  # noqa: B017 - 预占超过实物违反 CHECK
            _run(_reject_negative())
        assert _run(_stop_at_available()) == 3


class TestHsBindingIsolation:
    def test_tenant_cannot_read_another_tenants_hs_binding(self, migrated: None) -> None:
        base = 970000000000000000 + time.time_ns() % 10**12
        spu_a, spu_b = base, base + 1
        bind_a, bind_b = base + 2, base + 3

        async def _hs_id() -> int:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                code_id = (await session.execute(text("SELECT id FROM hs_code WHERE code = '610910'"))).scalar_one()
            await engine.dispose()
            return int(code_id)

        hs_id = _run(_hs_id())

        async def _seed() -> None:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                for spu_id, tenant_id, title in (
                    (spu_a, TENANT_A, "hs-product-a"),
                    (spu_b, TENANT_B, "hs-product-b"),
                ):
                    await session.execute(
                        text("INSERT INTO spu (id, tenant_id, title, status) VALUES (:id, :tid, :title, 'DRAFT')"),
                        {"id": spu_id, "tid": tenant_id, "title": title},
                    )
                for bind_id, tenant_id, spu_id, market in (
                    (bind_a, TENANT_A, spu_a, "US"),
                    (bind_b, TENANT_B, spu_b, "SG"),
                ):
                    await session.execute(
                        text(
                            "INSERT INTO spu_hs_binding ("
                            "id, tenant_id, spu_id, market, hs_code_id, basis"
                            ") VALUES (:id, :tid, :spu, :market, :hs, :basis)"
                        ),
                        {
                            "id": bind_id,
                            "tid": tenant_id,
                            "spu": spu_id,
                            "market": market,
                            "hs": hs_id,
                            "basis": "棉质针织",
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
                            text("SELECT id FROM spu_hs_binding WHERE id IN (:a, :b)"),
                            {"a": bind_a, "b": bind_b},
                        )
                    )
                    .scalars()
                    .all()
                )
            await engine.dispose()
            return {int(row) for row in rows}

        async def _delete_binding() -> None:
            engine, factory = _app_session()
            async with factory() as session:
                await _bind(session, TENANT_A)
                await session.execute(text("DELETE FROM spu_hs_binding WHERE id = :id"), {"id": bind_a})
                await session.commit()
            await engine.dispose()

        _run(_seed())
        assert _run(_visible(TENANT_A)) == {bind_a}
        assert _run(_visible(TENANT_B)) == {bind_b}
        with pytest.raises(Exception):  # noqa: B017 - 绑定不提供删除，应用角色没有 DELETE
            _run(_delete_binding())

    def test_seeded_description_search_returns_within_one_second(self, migrated: None) -> None:
        import app.db.session  # noqa: F401 - 注册 ORM 租户监听
        from app.repositories.hs_code import HsCodeRepository

        async def _search(q: str) -> tuple[list[str], float]:
            engine, factory = _app_session()
            started = time.perf_counter()
            async with factory() as session:
                rows = await HsCodeRepository(session).search(q, limit=20)
            elapsed = time.perf_counter() - started
            await engine.dispose()
            return [row.code for row in rows], elapsed

        by_name, name_elapsed = _run(_search("T恤衫"))
        by_code, code_elapsed = _run(_search("610910"))
        assert by_name[0] == "610910"
        assert by_code[0] == "610910"
        assert name_elapsed < 1
        assert code_elapsed < 1


class TestComplianceConfigIsolation:
    def test_tenant_cannot_read_another_tenants_tax_rule(self, migrated: None) -> None:
        _assert_hidden(
            "country_tax_rule",
            (
                "id, tenant_id, country, tax_type, hs_code_pattern, rate, basis_numerator, "
                "basis_denominator, effective_from, version, status, source, verified_by, verified_at"
            ),
            "(:id, :tid, 'SG', 'GST', :pattern, 0.010000, 1, 1, DATE '2026-01-01', 1, 'ACTIVE', 'test source', 1, now())",
        )

    def test_tenant_cannot_read_another_tenants_certificate(self, migrated: None) -> None:
        base = 971000000000000000 + time.time_ns() % 10**12

        async def _seed() -> tuple[int, int]:
            engine = create_async_engine(settings.database_migration_url, poolclass=None)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                for offset, tenant_id in ((0, TENANT_A), (1, TENANT_B)):
                    await session.execute(
                        text("INSERT INTO spu (id, tenant_id, title, status) VALUES (:id, :tid, :title, 'DRAFT')"),
                        {"id": base + offset, "tid": tenant_id, "title": f"cert-{offset}"},
                    )
                    await session.execute(
                        text(
                            "INSERT INTO sku ("
                            "id, tenant_id, spu_id, sku_code, weight_g, length_cm, width_cm, height_cm"
                            ") VALUES (:id, :tid, :spu, :code, 1, 1, 1, 1)"
                        ),
                        {
                            "id": base + 10 + offset,
                            "tid": tenant_id,
                            "spu": base + offset,
                            "code": f"CERT-{offset}-{base}",
                        },
                    )
                    await session.execute(
                        text(
                            "INSERT INTO compliance_certificate ("
                            "id, tenant_id, sku_id, market, cert_type, cert_no, issued_at, expires_at"
                            ") VALUES ("
                            ":id, :tid, :sku, 'US', 'FCC', :cert_no, DATE '2024-01-01', DATE '2028-01-01'"
                            ")"
                        ),
                        {
                            "id": base + 20 + offset,
                            "tid": tenant_id,
                            "sku": base + 10 + offset,
                            "cert_no": f"FCC-{offset}",
                        },
                    )
                await session.commit()
            await engine.dispose()
            return base + 20, base + 21

        cert_a, cert_b = _run(_seed())
        assert _run(_visible("compliance_certificate", cert_a, cert_b, TENANT_A)) == {cert_a}
        assert _run(_visible("compliance_certificate", cert_a, cert_b, TENANT_B)) == {cert_b}
        with pytest.raises(Exception):  # noqa: B017 - 台账不提供删除
            _run(_delete("compliance_certificate", cert_a))

    def test_tenant_cannot_read_another_tenants_cert_requirement(self, migrated: None) -> None:
        _assert_hidden(
            "cert_requirement_rule",
            "id, tenant_id, market, category_code, cert_type, source, status",
            "(:id, :tid, 'US', :category, 'FCC', 'operator note', 'ACTIVE')",
        )

    def test_tenant_cannot_read_another_tenants_compliance_notice(self, migrated: None) -> None:
        _assert_hidden(
            "compliance_notice",
            "id, tenant_id, kind, level, ref_id, due_on, summary",
            "(:id, :tid, 'TAX_EFFECTIVE', 'D7', :ref, DATE '2026-10-16', 'notice')",
        )


def _assert_hidden(table: str, columns: str, values: str) -> None:
    base = 972000000000000000 + time.time_ns() % 10**12
    row_a, row_b = base, base + 1

    async def _seed() -> None:
        engine = create_async_engine(settings.database_migration_url, poolclass=None)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            for row_id, tenant_id in ((row_a, TENANT_A), (row_b, TENANT_B)):
                await session.execute(
                    text(f"INSERT INTO {table} ({columns}) VALUES {values}"),
                    {
                        "id": row_id,
                        "tid": tenant_id,
                        "pattern": f"{base % 100000000:08d}",
                        "category": f"c{base % 100000000}",
                        "ref": base,
                    },
                )
            await session.commit()
        await engine.dispose()

    _run(_seed())
    assert _run(_visible(table, row_a, row_b, TENANT_A)) == {row_a}
    assert _run(_visible(table, row_a, row_b, TENANT_B)) == {row_b}
    with pytest.raises(Exception):  # noqa: B017 - 应用角色没有 DELETE
        _run(_delete(table, row_a))


async def _visible(table: str, row_a: int, row_b: int, tenant_id: int) -> set[int]:
    engine, factory = _app_session()
    async with factory() as session:
        await _bind(session, tenant_id)
        rows = (
            (
                await session.execute(
                    text(f"SELECT id FROM {table} WHERE id IN (:a, :b)"),
                    {"a": row_a, "b": row_b},
                )
            )
            .scalars()
            .all()
        )
    await engine.dispose()
    return {int(row) for row in rows}


async def _delete(table: str, row_id: int) -> None:
    engine, factory = _app_session()
    async with factory() as session:
        await _bind(session, TENANT_A)
        await session.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id})
        await session.commit()
    await engine.dispose()
