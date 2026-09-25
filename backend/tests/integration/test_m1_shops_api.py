"""M1-05～08：授权、首笔订单、解绑与租户隔离。"""

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
OWNER_PASSWORD = "Owner-Shop-Passw0rd!"


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


def _register_and_login(client: TestClient, prefix: str, *, verify: bool) -> tuple[str, dict[str, str]]:
    email = f"{prefix}@example.com"
    created = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": OWNER_PASSWORD,
            "tenant_code": prefix,
            "tenant_name": prefix,
        },
    )
    assert created.status_code == 201, created.text
    if verify:
        verified = client.post("/api/v1/auth/verify-email", json={"token": _token_from_last_outbox()})
        assert verified.status_code == 200, verified.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": OWNER_PASSWORD})
    assert login.status_code == 200, login.text
    return email, _auth(login.json()["data"]["access_token"])


def _bind(client: TestClient, headers: dict[str, str], platform: str, site: str) -> dict[str, object]:
    auth_url = client.post(
        f"/api/v1/shops/{platform}/auth-url",
        headers={**headers, "Idempotency-Key": _unique("auth")},
        json={"site_code": site},
    )
    assert auth_url.status_code == 200, auth_url.text
    state = auth_url.json()["data"]["state"]
    assert "code=fixture" in auth_url.json()["data"]["url"]
    bound = client.post(
        f"/api/v1/shops/callback/{platform}",
        headers={**headers, "Idempotency-Key": _unique("cb")},
        json={"code": "fixture", "state": state},
    )
    assert bound.status_code == 200, bound.text
    return bound.json()["data"]


def test_unverified_email_cannot_bind_shop(client: TestClient) -> None:
    _email, headers = _register_and_login(client, _unique("shopnv"), verify=False)
    denied = client.post(
        "/api/v1/shops/shopee/auth-url",
        headers=headers,
        json={"site_code": "SG"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == int(ErrorCode.EMAIL_NOT_VERIFIED)


def test_shop_grant_sync_unbind_and_isolation(client: TestClient) -> None:
    _owner_email, headers = _register_and_login(client, _unique("shopa"), verify=True)
    _other_email, other_headers = _register_and_login(client, _unique("shopb"), verify=True)

    catalog = client.get("/api/v1/shops/catalog", headers=headers)
    assert catalog.status_code == 200, catalog.text
    lazada = next(item for item in catalog.json()["data"] if item["code"] == "lazada")
    assert set(lazada["sites"]) == {"SG", "MY", "TH", "ID", "VN", "PH"}

    unsupported = client.post(
        "/api/v1/shops/lazada/auth-url",
        headers=headers,
        json={"site_code": "US"},
    )
    assert unsupported.status_code == 422

    sg = _bind(client, headers, "shopee", "SG")
    my = _bind(client, headers, "shopee", "MY")
    assert sg["platform_shop_id"] != my["platform_shop_id"]
    assert sg["health"] == "yellow"

    replay = client.post(
        "/api/v1/shops/callback/shopee",
        headers=headers,
        json={"code": "fixture", "state": "a" * 20},
    )
    assert replay.status_code == 400
    assert replay.json()["code"] == int(ErrorCode.GRANT_FAILED)

    synced = client.post(
        f"/api/v1/shops/{sg['id']}/sync",
        headers={**headers, "Idempotency-Key": _unique("sync")},
        json={"module": "order"},
    )
    assert synced.status_code == 200, synced.text
    task = synced.json()["data"]
    assert task["status"] == 2
    assert task["stats"]["pulled"] == 1
    first = task["stats"]["first_order"]
    assert first["platform_order_id"] == "2601150000001"
    assert first["total_amount"] == "19.900000"
    assert isinstance(first["total_amount"], str)

    listed = client.get("/api/v1/shops", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"]["page_info"]["total"] == 2
    refreshed = next(item for item in listed.json()["data"]["items"] if item["id"] == sg["id"])
    assert refreshed["health"] == "green"

    product = client.post(
        f"/api/v1/shops/{sg['id']}/sync",
        headers=headers,
        json={"module": "product"},
    )
    assert product.status_code == 200, product.text
    assert product.json()["data"]["status"] == 3
    assert "M3" in product.json()["data"]["error"]

    hidden = client.get(f"/api/v1/shops/{sg['id']}", headers=other_headers)
    assert hidden.status_code == 404
    assert hidden.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)

    logs = client.get("/api/v1/sync-tasks", headers=headers, params={"shop_id": sg["id"]})
    assert logs.status_code == 200, logs.text
    assert len(logs.json()["data"]["items"]) >= 2
    assert (
        client.get("/api/v1/sync-tasks", headers=other_headers, params={"shop_id": sg["id"]}).json()["data"]["items"]
        == []
    )

    missing = client.delete(f"/api/v1/shops/{sg['id']}", headers=headers)
    assert missing.status_code == 428
    confirmation = client.post(
        "/api/v1/auth/confirm-password",
        headers=headers,
        json={"password": OWNER_PASSWORD, "action": "shop.unbind"},
    )
    assert confirmation.status_code == 200, confirmation.text
    removed = client.delete(
        f"/api/v1/shops/{sg['id']}",
        headers={
            **headers,
            "X-Confirmation-Token": confirmation.json()["data"]["confirmation_token"],
            "Idempotency-Key": _unique("unbind"),
        },
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["data"]["status"] == "unbound"
    assert client.get(f"/api/v1/shops/{sg['id']}", headers=headers).status_code == 404

    audit = client.get("/api/v1/audit-logs", headers=headers, params={"resource": "shop", "limit": 20})
    actions = {row["action"] for row in audit.json()["data"]["items"]}
    assert {"SHOP_GRANT", "SHOP_REVOKE"} <= actions
