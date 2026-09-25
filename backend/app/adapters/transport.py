"""平台 HTTP 出口。fixture 模式不访问外网；live 模式才发真实请求。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.adapters.errors import AdapterError, classify_platform_error
from app.core.config import settings

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


class PlatformTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        platform: str,
    ) -> tuple[int, dict[str, Any]]: ...


def use_fixture_transport() -> bool:
    return settings.platform_transport != "live"


def load_json_fixture(name: str) -> dict[str, Any]:
    path = _FIXTURE_DIR / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AdapterError(f"fixture {name} 不是对象", decision=classify_platform_error(platform="", http_status=None))
    return payload


class FixtureTransport:
    """用脱敏合成报文代替平台。凭证到位后只切 ``platform_transport=live``。"""

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        platform: str,
    ) -> tuple[int, dict[str, Any]]:
        del method
        body = json_body or {}
        grant = body.get("grant_type") or (params or {}).get("grant_type")
        if "/orders" in url or "/order/" in url:
            page = load_json_fixture("orders.json")
            raw = page.get(platform)
            if not isinstance(raw, dict):
                raise AdapterError(f"{platform} 缺少订单 fixture", platform=platform)
            return 200, raw
        token = load_json_fixture("token.json")
        if grant == "refresh_token":
            token = {**token, "access_token": f"{token['access_token']}-refreshed"}
        return 200, token


class LiveTransport:
    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        platform: str,
    ) -> tuple[int, dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.request(method, url, params=params, json=json_body)
        except httpx.HTTPError as exc:
            raise AdapterError(
                "平台连接失败",
                platform=platform,
                decision=classify_platform_error(platform=platform, http_status=None),
            ) from exc
        try:
            payload = response.json()
        except json.JSONDecodeError:
            payload = {"raw": response.text[:500]}
        if not isinstance(payload, dict):
            payload = {"items": payload}
        if response.status_code >= 400:
            raise AdapterError(
                f"平台返回 HTTP {response.status_code}",
                platform=platform,
                http_status=response.status_code,
                platform_code=str(payload.get("code") or payload.get("error") or ""),
                decision=classify_platform_error(platform=platform, http_status=response.status_code),
                raw=payload,
            )
        return response.status_code, payload


def default_transport() -> PlatformTransport:
    if use_fixture_transport():
        return FixtureTransport()
    return LiveTransport()


__all__ = [
    "FixtureTransport",
    "LiveTransport",
    "PlatformTransport",
    "default_transport",
    "load_json_fixture",
    "use_fixture_transport",
]
