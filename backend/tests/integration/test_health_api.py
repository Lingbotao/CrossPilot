"""API 契约测试（不需要数据库）。

验证的是"接口外壳"：统一响应信封、trace_id、错误码映射、未认证拦截。
这些是前端每天都要依赖的东西，一旦漂移，前端会集体报错，
所以放在 CI 第一道关卡里守着。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ErrorCode
from app.core.middleware import TRACE_HEADER
from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


class TestHealthEndpoints:
    def test_healthz_returns_envelope(self, client: TestClient) -> None:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["status"] == "ok"
        assert body["data"]["version"]

    def test_healthz_does_not_touch_database(self, client: TestClient) -> None:
        """存活探针绝不能依赖数据库 —— 否则库一抖，K8s 会把正常 Pod 杀光。"""
        assert client.get("/healthz").status_code == 200

    def test_trace_id_header_present(self, client: TestClient) -> None:
        resp = client.get("/healthz")
        assert resp.headers.get(TRACE_HEADER)

    def test_client_supplied_trace_id_is_echoed(self, client: TestClient) -> None:
        resp = client.get("/healthz", headers={TRACE_HEADER: "my-trace-0001"})
        assert resp.headers[TRACE_HEADER] == "my-trace-0001"

    def test_malicious_trace_id_is_replaced(self, client: TestClient) -> None:
        """拒绝超长/含非法字符的 trace_id —— 防日志注入。"""
        resp = client.get("/healthz", headers={TRACE_HEADER: "x" * 500})
        assert resp.headers[TRACE_HEADER] != "x" * 500

    def test_version_endpoint(self, client: TestClient) -> None:
        body = client.get("/version").json()
        assert body["code"] == 0
        assert body["data"]["api_prefix"] == "/api/v1"


class TestAuthGuard:
    @pytest.mark.parametrize(
        "path",
        ["/api/v1/auth/me", "/api/v1/tenants/current"],
    )
    def test_protected_endpoints_require_token(self, client: TestClient, path: str) -> None:
        resp = client.get(path)
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == int(ErrorCode.UNAUTHENTICATED)
        assert body["trace_id"]

    def test_invalid_token_rejected(self, client: TestClient) -> None:
        resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
        assert resp.status_code == 401
        assert resp.json()["code"] == int(ErrorCode.UNAUTHENTICATED)


class TestErrorContract:
    def test_unknown_route_returns_envelope(self, client: TestClient) -> None:
        resp = client.get("/api/v1/definitely-not-a-route")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)
        assert body["trace_id"], "404 也必须带 trace_id，否则用户报错时无法定位"

    def test_validation_error_maps_to_10001(self, client: TestClient) -> None:
        resp = client.post("/api/v1/auth/login", json={"email": "not-an-email"})
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == int(ErrorCode.PARAM_INVALID)
        assert "fields" in body["data"]

    def test_validation_error_lists_field_names(self, client: TestClient) -> None:
        resp = client.post("/api/v1/auth/login", json={"email": "a@b.com"})
        fields = resp.json()["data"]["fields"]
        assert any("password" in key for key in fields)

    def test_method_not_allowed_is_wrapped(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/auth/login")
        assert resp.status_code == 405
        assert resp.json()["code"] != 0


class TestOpenApi:
    def test_schema_is_served(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        assert schema["info"]["title"] == "CrossPilot API"
        for route in ("/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/tenants/current"):
            assert route in schema["paths"], f"{route} 未出现在 OpenAPI 契约中"

    def test_root_redirects_to_docs(self, client: TestClient) -> None:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/docs"
