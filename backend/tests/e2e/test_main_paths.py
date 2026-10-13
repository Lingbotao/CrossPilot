"""M6-04：8 条 API 主路径。平台调用保持 fixture。

刊登和 Webhook 的受理走 HTTP。真正入库的 worker 在请求返回后于本进程执行，
因为这套测试不启动 Celery。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.adapters.signing import shopee_push_sign
from app.core.context import tenant_context
from app.core.errors import ErrorCode
from app.main import app
from app.services.outbox import messages
from app.services.webhook_dispatch import _ingest
from app.tasks.batch import _run_publish

pytestmark = pytest.mark.needs_db

OWNER_PASSWORD = "Owner-M6-Passw0rd!"
MEMBER_PASSWORD = "Member-M6-Passw0rd!"
SHOPEE_ORDER = "2601150000001"


def _unique(prefix: str) -> str:
    return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _data(response: object, status: int = 200) -> dict[str, object]:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert isinstance(data, dict)
    return data


def _register_and_login(client: TestClient, prefix: str) -> dict[str, str]:
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
    verified = client.post("/api/v1/auth/verify-email", json={"token": _token_from_last_outbox()})
    assert verified.status_code == 200, verified.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": OWNER_PASSWORD})
    assert login.status_code == 200, login.text
    return _auth(login.json()["data"]["access_token"])


def _token_from_last_outbox() -> str:
    return parse_qs(urlparse(messages()[-1].url).query)["token"][0]


def _bind(client: TestClient, headers: dict[str, str], platform: str, site: str) -> dict[str, object]:
    auth_url = client.post(
        f"/api/v1/shops/{platform}/auth-url",
        headers={**headers, "Idempotency-Key": _unique("auth")},
        json={"site_code": site},
    )
    state = _data(auth_url)["state"]
    bound = client.post(
        f"/api/v1/shops/callback/{platform}",
        headers={**headers, "Idempotency-Key": _unique("cb")},
        json={"code": "fixture", "state": state},
    )
    return _data(bound)


def _sku(client: TestClient, headers: dict[str, str], code: str) -> tuple[str, str]:
    created = client.post(
        "/api/v1/spus",
        headers={**headers, "Idempotency-Key": _unique("spu")},
        json={
            "title": f"主路径商品 {code}",
            "status": "ON_SALE",
            "skus": [
                {
                    "sku_code": code,
                    "weight_g": "200",
                    "length_cm": "10",
                    "width_cm": "8",
                    "height_cm": "4",
                    "purchase_price": "12.500000",
                    "currency": "CNY",
                }
            ],
        },
    )
    detail = _data(created)
    skus = detail["skus"]
    assert isinstance(skus, list) and skus
    return str(detail["id"]), str(skus[0]["id"])


def _sync_orders(client: TestClient, headers: dict[str, str], shop_id: str) -> dict[str, object]:
    synced = client.post(
        f"/api/v1/shops/{shop_id}/sync",
        headers={**headers, "Idempotency-Key": _unique("sync")},
        json={"module": "order"},
    )
    task = _data(synced)
    stats = task["stats"]
    assert isinstance(stats, dict)
    return stats


def test_register_login_and_tenant_context(client: TestClient) -> None:
    headers = _register_and_login(client, _unique("m6auth"))
    current = _data(client.get("/api/v1/tenants/current", headers=headers))
    assert current["role_code"] == "OWNER"
    assert current["can_view_cost"] is True
    permissions = current["permissions"]
    assert isinstance(permissions, list)
    assert "order:read" in permissions
    assert "product:write" in permissions


def test_fixture_shop_grant_is_listed_with_sync_tasks(client: TestClient) -> None:
    headers = _register_and_login(client, _unique("m6shop"))
    shop = _bind(client, headers, "shopee", "SG")
    listed = _data(client.get("/api/v1/shops", headers=headers))
    items = listed["items"]
    assert isinstance(items, list)
    assert any(item["id"] == shop["id"] for item in items)
    _sync_orders(client, headers, str(shop["id"]))
    tasks = _data(client.get("/api/v1/sync-tasks", headers=headers, params={"shop_id": shop["id"]}))
    task_items = tasks["items"]
    assert isinstance(task_items, list) and task_items
    assert task_items[0]["shop_id"] == shop["id"]


def test_publish_blocks_without_hs_then_passes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.tasks.batch.enqueue_publish", lambda *_args, **_kwargs: None)
    headers = _register_and_login(client, _unique("m6pub"))
    current = _data(client.get("/api/v1/tenants/current", headers=headers))
    tenant_id = int(str(current["id"]))
    _spu_id, sku_id = _sku(client, headers, _unique("sku")[-12:])
    shop = _bind(client, headers, "shopee", "SG")

    first = _accept_publish(client, headers, sku_id, str(shop["id"]))
    listing_id = str(first["items"][0]["listing_id"])  # type: ignore[index]
    content = _data(
        client.post(
            "/api/v1/listing-contents",
            headers={**headers, "Idempotency-Key": _unique("copy")},
            json={
                "listing_id": listing_id,
                "lang": "en",
                "title": "Reviewed cup",
                "description": "A cup",
                "confirm_review": True,
            },
        )
    )
    published = client.post(
        f"/api/v1/listing-contents/{content['id']}/publish",
        headers={**headers, "Idempotency-Key": _unique("lang")},
    )
    assert _data(published)["quality_status"] == "PUBLISHED"

    blocked = _finish_publish(client, headers, first, tenant_id)
    assert blocked["failed"] == 1
    message = str(blocked["items"][0]["error_message"])  # type: ignore[index]
    assert "HS" in message

    hits = client.get("/api/v1/hs-codes/search", headers=headers, params={"q": "610910", "limit": 5})
    assert hits.status_code == 200, hits.text
    hs_rows = hits.json()["data"]
    assert hs_rows
    bound = client.post(
        f"/api/v1/spus/{_spu_id}/hs-code",
        headers={**headers, "Idempotency-Key": _unique("hs")},
        json={"market": "SG", "hs_code_id": hs_rows[0]["id"], "basis": "主路径核对 WCO 2022 编码"},
    )
    assert bound.status_code == 200, bound.text

    second = _accept_publish(client, headers, sku_id, str(shop["id"]))
    passed = _finish_publish(client, headers, second, tenant_id)
    assert passed["succeeded"] == 1
    assert passed["failed"] == 0


def test_poll_and_webhook_keep_one_order_then_ship(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, str]] = []
    monkeypatch.setattr("app.services.webhook_ingress.publish_webhook", captured.append)
    headers = _register_and_login(client, _unique("m6ord"))
    sg = _bind(client, headers, "shopee", "SG")
    my = _bind(client, headers, "shopee", "MY")
    first = _sync_orders(client, headers, str(sg["id"]))
    assert first["inserted"] == 1
    assert first["first_order"]["total_amount"] == "19.900000"  # type: ignore[index]
    assert isinstance(first["first_order"]["total_amount"], str)  # type: ignore[index]

    body = json.dumps(
        {
            "msg_id": _unique("msg"),
            "shop_id": sg["platform_shop_id"],
            "code": 3,
            "timestamp": 1768467600,
            "data": {"ordersn": SHOPEE_ORDER, "status": "READY_TO_SHIP", "update_time": 1768467600},
        },
        separators=(",", ":"),
    ).encode()
    callback = "http://testserver/api/v1/webhooks/shopee"
    accepted = client.post(
        "/api/v1/webhooks/shopee",
        content=body,
        headers={
            "content-type": "application/json",
            "authorization": shopee_push_sign(partner_key="fixture-secret", url=callback, body=body),
        },
    )
    assert _data(accepted)["accepted"] is True
    assert captured
    tenant_id = int(str(_data(client.get("/api/v1/tenants/current", headers=headers))["id"]))
    shop_id = int(str(sg["id"]))

    async def _apply() -> dict[str, object]:
        # fixture 店铺号在各租户间相同。按全局店铺号分发会撞上别的租户，这里直接对刚授权的店铺走 Webhook 入库。
        with tenant_context(tenant_id):
            return await _ingest(tenant_id=tenant_id, shop_id=shop_id, platform_order_id=SHOPEE_ORDER)

    applied = client.portal.call(_apply)
    assert applied["action"] in {"skip", "update"}

    sg_orders = _data(client.get("/api/v1/orders", headers=headers, params={"shop_id": sg["id"], "limit": 20}))
    sg_items = sg_orders["items"]
    assert isinstance(sg_items, list) and len(sg_items) == 1
    assert sg_items[0]["platform_order_id"] == SHOPEE_ORDER
    assert isinstance(sg_items[0]["total_amount"], str)

    _sync_orders(client, headers, str(my["id"]))
    page = _data(client.get("/api/v1/orders", headers=headers, params={"limit": 1}))
    info = page["page_info"]
    assert isinstance(info, dict) and info["has_more"] is True
    assert info["cursor"]
    nxt = _data(client.get("/api/v1/orders", headers=headers, params={"limit": 1, "cursor": info["cursor"]}))
    first_ids = {item["id"] for item in page["items"]}  # type: ignore[index]
    next_ids = {item["id"] for item in nxt["items"]}  # type: ignore[index]
    assert first_ids.isdisjoint(next_ids)
    assert len(first_ids | next_ids) == 2

    shipped = client.post(
        "/api/v1/orders/batch-ship",
        headers={**headers, "Idempotency-Key": _unique("ship")},
        json={"order_ids": [sg_items[0]["id"]], "carrier": "Shopee Xpress"},
    )
    result = _data(shipped)
    assert result["succeeded"] == 1
    assert result["failed"] == 0


def test_order_keyword_filter_binds_the_parameter(client: TestClient) -> None:
    headers = _register_and_login(client, _unique("m6inj"))
    shop = _bind(client, headers, "shopee", "SG")
    _sync_orders(client, headers, str(shop["id"]))
    injected = client.get(
        "/api/v1/orders",
        headers=headers,
        params={"q": "' OR 1=1 --", "limit": 20},
    )
    assert injected.status_code == 200, injected.text
    assert injected.json()["code"] == 0
    assert injected.json()["data"]["items"] == []
    matched = client.get(
        "/api/v1/orders",
        headers=headers,
        params={"q": "fixture_buyer", "limit": 20},
    )
    rows = _data(matched)["items"]
    assert isinstance(rows, list) and len(rows) == 1


def test_parallel_outbound_does_not_go_negative(client: TestClient) -> None:
    headers = _register_and_login(client, _unique("m6stk"))
    _spu_id, sku_id = _sku(client, headers, _unique("stk")[-12:])
    warehouse = _data(
        client.post(
            "/api/v1/warehouses",
            headers={**headers, "Idempotency-Key": _unique("wh")},
            json={"name": "主路径仓", "warehouse_type": "LOCAL", "country": "CN", "is_default": True},
        )
    )
    inbound = client.post(
        "/api/v1/inventories/adjust",
        headers={**headers, "Idempotency-Key": _unique("in")},
        json={
            "sku_id": sku_id,
            "warehouse_id": warehouse["id"],
            "kind": "INBOUND",
            "quantity": 1,
        },
    )
    assert _data(inbound)["available"] == 1

    def outbound() -> int:
        response = client.post(
            "/api/v1/inventories/adjust",
            headers={**headers, "Idempotency-Key": _unique("out")},
            json={
                "sku_id": sku_id,
                "warehouse_id": warehouse["id"],
                "kind": "OUTBOUND",
                "quantity": 1,
            },
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _n: outbound(), (1, 2)))
    assert sorted(codes) == [200, 409]
    stock = _data(
        client.get(
            "/api/v1/inventories",
            headers=headers,
            params={"sku_id": sku_id, "warehouse_id": warehouse["id"]},
        )
    )
    rows = stock["items"]
    assert isinstance(rows, list) and rows[0]["available"] == 0
    assert rows[0]["available"] >= 0


def test_landed_cost_returns_formula_source_and_string_money(client: TestClient) -> None:
    headers = _register_and_login(client, _unique("m6lc"))
    calculated = client.post(
        "/api/v1/landed-cost/calculations",
        headers={**headers, "Idempotency-Key": _unique("calc")},
        json={
            "market": "SG",
            "selling_currency": "SGD",
            "channel": "AIR",
            "first_mile_method": "WEIGHT",
            "name": "主路径",
            "selling_price": "20.000000",
            "purchase_amount": "12.500000",
            "purchase_currency": "SGD",
            "weight_g": "500",
            "declared_value": "12.500000",
            "declared_currency": "SGD",
        },
    )
    view = _data(calculated)
    assert view["landed_cost"] is None or isinstance(view["landed_cost"], str)
    lines = view["lines"]
    assert isinstance(lines, list) and lines
    for line in lines:
        assert isinstance(line["formula"], str) and line["formula"]
        assert isinstance(line["source"], str) and line["source"]
        assert line["amount"] is None or isinstance(line["amount"], str)


def test_purchase_and_first_mile_write_the_cost_pool(client: TestClient) -> None:
    headers = _register_and_login(client, _unique("m6po"))
    _spu_id, sku_id = _sku(client, headers, _unique("po")[-12:])
    warehouse = _data(
        client.post(
            "/api/v1/warehouses",
            headers={**headers, "Idempotency-Key": _unique("wh")},
            json={"name": "采购仓", "warehouse_type": "LOCAL", "country": "CN"},
        )
    )
    supplier = _data(
        client.post(
            "/api/v1/suppliers",
            headers={**headers, "Idempotency-Key": _unique("sup")},
            json={"name": "主路径供应商", "settlement_type": "PREPAY"},
        )
    )
    order = _data(
        client.post(
            "/api/v1/purchase-orders",
            headers={**headers, "Idempotency-Key": _unique("po")},
            json={
                "supplier_id": supplier["id"],
                "warehouse_id": warehouse["id"],
                "currency": "CNY",
                "lines": [{"sku_id": sku_id, "quantity": 2, "unit_price": "12.500000"}],
            },
        )
    )
    assert order["status"] == "DRAFT"
    lines = order["lines"]
    assert isinstance(lines, list)
    assert lines[0]["unit_price"] == "12.500000"
    today = datetime.now(UTC).date().isoformat()
    source = "主路径测试配置，不是法定税率"
    for tax_type, rate in (("DUTY", "0.080000"), ("GST", "0.090000")):
        rule = client.post(
            "/api/v1/tax-rules",
            headers={**headers, "Idempotency-Key": _unique(tax_type.lower())},
            json={
                "country": "SG",
                "tax_type": tax_type,
                "hs_code_pattern": "*",
                "rate": rate,
                "effective_from": today,
                "source": source,
            },
        )
        assert rule.status_code == 200, rule.text
    for fee_code, charge, amount, currency in (
        ("BROKERAGE", "FIXED", "8.000000", "CNY"),
        ("FX_RESERVE", "RATE", "0.020000", None),
    ):
        fee = client.post(
            "/api/v1/landed-cost/fees",
            headers={**headers, "Idempotency-Key": _unique(fee_code.lower())},
            json={
                "market": "SG",
                "channel": "AIR",
                "fee_code": fee_code,
                "charge": charge,
                "amount": amount,
                "currency": currency,
                "effective_from": today,
                "source": source,
            },
        )
        assert fee.status_code == 200, fee.text

    shipment = _data(
        client.post(
            "/api/v1/first-mile-shipments",
            headers={**headers, "Idempotency-Key": _unique("fm")},
            json={
                "forwarder": "主路径货代",
                "channel": "AIR",
                "destination_market": "SG",
                "cost_total": "40.000000",
                "currency": "CNY",
                "alloc_method": "WEIGHT",
                "lines": [{"sku_id": sku_id, "quantity": 2}],
            },
        )
    )
    posted = client.post(
        f"/api/v1/first-mile-shipments/{shipment['id']}/post",
        headers={**headers, "Idempotency-Key": _unique("post")},
    )
    done = _data(posted)
    assert done["status"] == "POSTED"
    pools = done["pools"]
    allocations = done["allocations"]
    assert isinstance(pools, list) and pools
    assert isinstance(allocations, list) and allocations
    assert isinstance(pools[0]["first_mile_unit"], str)
    assert pools[0]["source"]
    assert isinstance(allocations[0]["allocated_cost"], str)
    assert allocations[0]["formula"]


def test_dashboard_hides_cost_from_ops_and_cs_stays_read_only(client: TestClient) -> None:
    headers = _register_and_login(client, _unique("m6ops"))
    member_email = f"{_unique('ops')}@example.com"
    invited = client.post(
        "/api/v1/members/invite",
        headers={**headers, "Idempotency-Key": _unique("invite")},
        json={"email": member_email, "role_code": "OPS_STAFF"},
    )
    assert invited.status_code == 200, invited.text
    accepted = client.post(
        "/api/v1/members/invitations/accept",
        json={"token": _token_from_last_outbox(), "password": MEMBER_PASSWORD, "display_name": "Ops"},
    )
    assert accepted.status_code == 200, accepted.text
    ops_login = client.post("/api/v1/auth/login", json={"email": member_email, "password": MEMBER_PASSWORD})
    ops = _auth(ops_login.json()["data"]["access_token"])
    ops_ctx = _data(client.get("/api/v1/tenants/current", headers=ops))
    assert ops_ctx["role_code"] == "OPS_STAFF"
    assert ops_ctx["can_view_cost"] is False

    shop = _bind(client, headers, "shopee", "SG")
    _sync_orders(client, headers, str(shop["id"]))
    today = datetime.now(UTC).date()
    window = {"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}
    rebuilt = client.post("/api/v1/dashboard/rebuild", headers=headers, json=window)
    assert rebuilt.status_code == 200, rebuilt.text
    overview = _data(client.get("/api/v1/dashboard/overview", headers=ops, params=window))
    currencies = overview["currencies"]
    assert isinstance(currencies, list)
    assert all(row["net_profit"] is None and row["ad_spend"] is None for row in currencies)

    hidden = client.get("/api/v1/landed-cost/fees", headers=ops)
    assert hidden.status_code == 403
    assert hidden.json()["code"] == int(ErrorCode.PERMISSION_DENIED)
    visible = client.get("/api/v1/landed-cost/fees", headers=headers)
    assert visible.status_code == 200, visible.text

    synced = client.post(
        "/api/v1/cs/messages/sync",
        headers={**headers, "Idempotency-Key": _unique("cs")},
        json={"shop_id": shop["id"]},
    )
    assert _data(synced)["messages"] >= 1  # type: ignore[operator]
    messages_page = _data(client.get("/api/v1/cs/messages", headers=ops, params={"limit": 20}))
    rows = messages_page["items"]
    assert isinstance(rows, list) and rows
    assert str(rows[0]["console_url"]).startswith("http")
    reply = client.post(f"/api/v1/cs/messages/{rows[0]['id']}/reply", headers=ops, json={"body": "hi"})
    assert reply.status_code == 404
    assert not any("reply" in path for path in app.openapi()["paths"])


def _accept_publish(client: TestClient, headers: dict[str, str], sku_id: str, shop_id: str) -> dict[str, object]:
    accepted = client.post(
        "/api/v1/listing-batches/publish",
        headers={**headers, "Idempotency-Key": _unique("pub")},
        json={"sku_ids": [sku_id], "shop_ids": [shop_id], "price": "19.900000", "currency": "SGD"},
    )
    return _data(accepted)


def _finish_publish(
    client: TestClient,
    headers: dict[str, str],
    batch: dict[str, object],
    tenant_id: int,
) -> dict[str, object]:
    async def _go() -> None:
        with tenant_context(tenant_id):
            await _run_publish(int(str(batch["id"])), tenant_id)

    client.portal.call(_go)
    return _data(client.get(f"/api/v1/listing-batches/{batch['id']}", headers=headers))
