"""推送报文的公共小工具。平台字段怎么读，仍留在各自适配器里。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.adapters.base import PageResult, UnifiedOrder
from app.adapters.errors import AdapterError, RetryDecision


def header_value(headers: Mapping[str, str], name: str) -> str:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value.strip()
    return ""


def load_object(body: bytes, *, platform: str) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AdapterError("Webhook 不是合法 JSON", platform=platform, decision=RetryDecision.FAIL_FAST) from exc
    if not isinstance(payload, dict):
        raise AdapterError("Webhook 不是对象", platform=platform, decision=RetryDecision.FAIL_FAST)
    return payload


def coalesce_id(*parts: object) -> str:
    raw = ":".join(str(part).strip() for part in parts if part is not None and str(part).strip())
    if not raw:
        return hashlib.sha256(b"empty").hexdigest()
    if len(raw) <= 180 and not any(char.isspace() for char in raw):
        return raw
    return hashlib.sha256(raw.encode()).hexdigest()


def as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def unix_time(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)) or value == "":
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def matching_order(page: PageResult[UnifiedOrder], platform_order_id: str) -> UnifiedOrder | None:
    for item in page.items:
        if item.platform_order_id == platform_order_id:
            return item
    return None


def nested_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


__all__ = [
    "as_int",
    "coalesce_id",
    "header_value",
    "load_object",
    "matching_order",
    "nested_dict",
    "unix_time",
]
