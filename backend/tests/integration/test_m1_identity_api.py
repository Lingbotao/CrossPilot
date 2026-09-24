"""M1-01～04：注册、邀请、RBAC、二次确认与审计闭环。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.errors import ErrorCode
from app.main import app
from app.services.outbox import messages

pytestmark = pytest.mark.needs_db
BACKEND_DIR = Path(__file__).resolve().parents[2]
OWNER_PASSWORD = "Owner-M1-Passw0rd!"
MEMBER_PASSWORD = "Member-M1-Passw0rd!"


@pytest.fixture(scope="module")
def migrated(pg_available: bool) -> None:
    if not pg_available:
        pytest.skip("需要 PostgreSQL")
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


def _token_from_last_outbox() -> str:
    return parse_qs(urlparse(messages()[-1].url).query)["token"][0]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_registration_validation_conflicts_and_private_resend(client: TestClient) -> None:
    suffix = _unique("register")
    email = f"{suffix}@example.com"
    weak = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "weakpassword",
            "tenant_code": suffix,
            "tenant_name": "弱密码测试",
        },
    )
    assert weak.status_code == 422
    assert weak.json()["code"] == int(ErrorCode.PARAM_INVALID)

    created = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": OWNER_PASSWORD,
            "tenant_code": suffix,
            "tenant_name": "注册冲突测试",
        },
    )
    assert created.status_code == 201, created.text

    duplicate_email = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": OWNER_PASSWORD,
            "tenant_code": f"{suffix}-other",
            "tenant_name": "重复邮箱",
        },
    )
    assert duplicate_email.status_code == 409
    assert duplicate_email.json()["code"] == int(ErrorCode.EMAIL_ALREADY_REGISTERED)

    duplicate_code = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"other-{suffix}@example.com",
            "password": OWNER_PASSWORD,
            "tenant_code": suffix,
            "tenant_name": "重复租户码",
        },
    )
    assert duplicate_code.status_code == 409
    assert duplicate_code.json()["code"] == int(ErrorCode.TENANT_CODE_TAKEN)

    before = len(messages())
    resend = client.post(
        "/api/v1/auth/resend-verification",
        json={"email": f"unknown-{suffix}@example.com"},
    )
    assert resend.status_code == 200
    assert len(messages()) == before


def test_m1_identity_and_audit_vertical_slice(client: TestClient) -> None:
    suffix = _unique("m1")
    owner_email = f"{suffix}-owner@example.com"
    member_email = f"{suffix}-member@example.com"

    registered = client.post(
        "/api/v1/auth/register",
        json={
            "email": owner_email,
            "password": OWNER_PASSWORD,
            "tenant_code": suffix,
            "tenant_name": f"M1 测试 {suffix}",
        },
    )
    assert registered.status_code == 201, registered.text
    verify_token = _token_from_last_outbox()
    verified = client.post("/api/v1/auth/verify-email", json={"token": verify_token})
    assert verified.status_code == 200, verified.text

    login = client.post(
        "/api/v1/auth/login",
        json={"email": owner_email, "password": OWNER_PASSWORD},
    )
    assert login.status_code == 200, login.text
    owner_access = login.json()["data"]["access_token"]
    headers = _auth(owner_access)

    roles = client.get("/api/v1/roles", headers=headers)
    assert roles.status_code == 200, roles.text
    assert len(roles.json()["data"]) == 8

    invited = client.post(
        "/api/v1/members/invite",
        headers={**headers, "Idempotency-Key": _unique("invite")},
        json={"email": member_email, "role_code": "OPS_STAFF"},
    )
    assert invited.status_code == 200, invited.text
    invitation_token = _token_from_last_outbox()

    accepted = client.post(
        "/api/v1/members/invitations/accept",
        json={
            "token": invitation_token,
            "password": MEMBER_PASSWORD,
            "display_name": "M1 Member",
        },
    )
    assert accepted.status_code == 200, accepted.text
    member_id = accepted.json()["data"]["member_id"]

    member_login = client.post(
        "/api/v1/auth/login",
        json={"email": member_email, "password": MEMBER_PASSWORD},
    )
    assert member_login.status_code == 200, member_login.text
    denied = client.get(
        "/api/v1/members",
        headers=_auth(member_login.json()["data"]["access_token"]),
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == int(ErrorCode.PERMISSION_DENIED)

    changed = client.patch(
        f"/api/v1/members/{member_id}/role",
        headers=headers,
        json={"role_code": "ADMIN"},
    )
    assert changed.status_code == 200, changed.text
    scoped = client.patch(
        f"/api/v1/members/{member_id}/data-scope",
        headers=headers,
        json={"resource_type": 1, "scope_type": 2, "shop_ids": ["900000000000001234"]},
    )
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["data"]["data_scope"]["1"]["scope_type"] == 2

    audit = client.get(
        "/api/v1/audit-logs",
        headers=headers,
        params={"resource": "member", "limit": 50},
    )
    assert audit.status_code == 200, audit.text
    actions = {row["action"] for row in audit.json()["data"]["items"]}
    assert {"MEMBER_INVITE", "MEMBER_ACCEPT", "MEMBER_ROLE_CHANGE", "MEMBER_SCOPE_CHANGE"} <= actions

    missing_confirmation = client.delete(f"/api/v1/members/{member_id}", headers=headers)
    assert missing_confirmation.status_code == 428
    assert missing_confirmation.json()["code"] == int(ErrorCode.CONFIRMATION_REQUIRED)

    confirmation = client.post(
        "/api/v1/auth/confirm-password",
        headers=headers,
        json={"password": OWNER_PASSWORD, "action": "member.remove"},
    )
    assert confirmation.status_code == 200, confirmation.text
    confirmation_token = confirmation.json()["data"]["confirmation_token"]
    delete_headers = {
        **headers,
        "X-Confirmation-Token": confirmation_token,
        "Idempotency-Key": _unique("remove"),
    }
    removed = client.delete(f"/api/v1/members/{member_id}", headers=delete_headers)
    assert removed.status_code == 200, removed.text

    replay = client.delete(f"/api/v1/members/{member_id}", headers=delete_headers)
    assert replay.status_code == 400
    assert replay.json()["code"] == int(ErrorCode.CONFIRMATION_INVALID)
