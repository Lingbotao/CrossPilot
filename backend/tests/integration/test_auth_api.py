"""登录失败落库、锁定、登出黑名单、Refresh 轮换（D1 / D2 / A-01 / A-02）。

需要真实 PostgreSQL（运行时角色受 RLS 约束）。本机无库时 skip。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.context import set_tenant_id
from app.core.errors import ErrorCode
from app.core.security import hash_password
from app.main import app
from app.models.enums import TenantPlan, TenantStatus, TenantUserStatus, UserStatus
from app.models.tenant import SysUser, Tenant, TenantUser

pytestmark = pytest.mark.needs_db

BACKEND_DIR = Path(__file__).resolve().parents[2]
_PASSWORD = "Auth-Test-Passw0rd!"


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


@pytest.fixture(scope="module")
def client(migrated: None) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _unique(prefix: str) -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"


def _owner_factory():
    engine = create_async_engine(settings.database_migration_url, poolclass=None)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _provision_user(email: str, *, tenant_code: str | None = None) -> tuple[int, int]:
    """用 owner 会话写入租户 + 用户 + 成员关系，返回 (user_id, tenant_id)。"""
    code = tenant_code or _unique("auth")
    engine, factory = _owner_factory()
    try:
        async with factory() as session:
            tenant = Tenant(
                code=code,
                name=f"Auth 测试 {code}",
                plan=int(TenantPlan.TRIAL),
                status=int(TenantStatus.ACTIVE),
                default_currency="CNY",
                timezone="Asia/Shanghai",
            )
            session.add(tenant)
            await session.flush()
            set_tenant_id(tenant.id)
            await session.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant.id)})

            user = SysUser(
                email=email,
                password_hash=hash_password(_PASSWORD),
                display_name="Auth Tester",
                status=int(UserStatus.ACTIVE),
            )
            session.add(user)
            await session.flush()

            session.add(
                TenantUser(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    role_code="OWNER",
                    status=int(TenantUserStatus.ACTIVE),
                    joined_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return int(user.id), int(tenant.id)
    finally:
        await engine.dispose()


def _login(client: TestClient, email: str, password: str = _PASSWORD) -> object:
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


class TestFailedLoginDoesNotFiveHundred:
    def test_unknown_email_returns_401(self, client: TestClient) -> None:
        resp = _login(client, "nobody-d1@example.com", "wrong-password")
        assert resp.status_code == 401, resp.text
        assert resp.json()["code"] == int(ErrorCode.UNAUTHENTICATED)

    def test_bad_password_returns_401_and_persists_lock_count(self, client: TestClient) -> None:
        email = f"{_unique('lock')}@example.com"
        user_id, _tenant_id = _run(_provision_user(email))

        resp = _login(client, email, "Definitely-Wrong-1")
        assert resp.status_code == 401, resp.text
        assert resp.json()["code"] == int(ErrorCode.UNAUTHENTICATED)

        async def _count() -> tuple[int, int]:
            engine, factory = _owner_factory()
            try:
                async with factory() as session:
                    user = (await session.execute(select(SysUser).where(SysUser.id == user_id))).scalar_one()
                    logs = int(
                        (
                            await session.execute(
                                text("SELECT count(*) FROM login_log WHERE email = :e AND result = 2"),
                                {"e": email},
                            )
                        ).scalar_one()
                    )
                    return int(user.failed_login_count), logs
            finally:
                await engine.dispose()

        failures, log_rows = _run(_count())
        assert failures == 1
        assert log_rows >= 1

    def test_lock_triggers_after_threshold(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "login_max_failures", 2)
        monkeypatch.setattr(settings, "login_lock_minutes", 15)
        email = f"{_unique('lock2')}@example.com"
        _run(_provision_user(email))

        assert _login(client, email, "Wrong-Once-1!").status_code == 401
        assert _login(client, email, "Wrong-Twice-1!").status_code == 401
        locked = _login(client, email, "Wrong-Third-1!")
        assert locked.status_code == 423, locked.text
        assert locked.json()["code"] == int(ErrorCode.ACCOUNT_LOCKED)


class TestLogoutAndRefreshRotation:
    def test_logout_revokes_access_and_refresh(self, client: TestClient) -> None:
        email = f"{_unique('out')}@example.com"
        _run(_provision_user(email))
        login = _login(client, email)
        assert login.status_code == 200, login.text
        tokens = login.json()["data"]

        out = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert out.status_code == 200, out.text

        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert me.status_code == 401, me.text

        refresh = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert refresh.status_code == 401, refresh.text

    def test_refresh_rotates_old_token_away(self, client: TestClient) -> None:
        email = f"{_unique('rot')}@example.com"
        _run(_provision_user(email))
        login = _login(client, email)
        first = login.json()["data"]["refresh_token"]

        rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": first})
        assert rotated.status_code == 200, rotated.text
        second = rotated.json()["data"]["refresh_token"]
        assert second != first

        replay = client.post("/api/v1/auth/refresh", json={"refresh_token": first})
        assert replay.status_code == 401, replay.text

    def test_locked_account_cannot_refresh(self, client: TestClient) -> None:
        email = f"{_unique('lkref')}@example.com"
        user_id, _tenant_id = _run(_provision_user(email))
        login = _login(client, email)
        assert login.status_code == 200, login.text
        refresh_token = login.json()["data"]["refresh_token"]

        async def _lock() -> None:
            engine, factory = _owner_factory()
            try:
                async with factory() as session:
                    user = (await session.execute(select(SysUser).where(SysUser.id == user_id))).scalar_one()
                    user.locked_until = datetime.now(UTC) + timedelta(minutes=30)
                    await session.commit()
            finally:
                await engine.dispose()

        _run(_lock())
        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 423, resp.text
        assert resp.json()["code"] == int(ErrorCode.ACCOUNT_LOCKED)
