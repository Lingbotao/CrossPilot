"""买家信息按角色脱敏（PRD F4）。

纯函数，不读库。导出、详情、面单都走这里，避免每个接口各写一套。
"""

from __future__ import annotations

from typing import Any

from app.core.permissions import RoleCode

_FULL = frozenset({RoleCode.OWNER.value, RoleCode.ADMIN.value})
_SHIP = frozenset({RoleCode.OPS_MANAGER.value, RoleCode.OPS_STAFF.value})
_REGION = frozenset({RoleCode.FINANCE.value})
_CONTACT = frozenset({RoleCode.CS.value})


def phone_last4(phone: str | None) -> str | None:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) < 4:
        return None
    return digits[-4:]


def mask_name_initial(name: str | None) -> str | None:
    if not name:
        return None
    return name[0] + ("*" * max(len(name) - 1, 1))


def mask_phone_middle(phone: str | None) -> str | None:
    """中间 4 位打码。位数不够时整段打码，避免短号被还原。"""

    if not phone:
        return None
    digit_at = [index for index, char in enumerate(phone) if char.isdigit()]
    if len(digit_at) < 7:
        return "*" * len(phone)
    start = (len(digit_at) - 4) // 2
    hidden = set(digit_at[start : start + 4])
    return "".join("*" if index in hidden else char for index, char in enumerate(phone))


def mask_party(
    role_code: str, buyer: dict[str, Any] | None, ship_to: dict[str, Any] | None
) -> tuple[dict[str, str | None], dict[str, str | None]]:
    """返回 (买家, 地址)。看不到的字段是 None，不返回空串冒充有值。"""

    source_buyer = buyer or {}
    source_ship = ship_to or {}
    name = _text(source_buyer.get("name"))
    phone = _text(source_buyer.get("phone") or source_ship.get("phone"))
    country = _text(source_buyer.get("country") or source_ship.get("country"))
    state = _text(source_ship.get("state"))
    city = _text(source_ship.get("city"))
    line1 = _text(source_ship.get("line1"))
    postal = _text(source_ship.get("postal_code") or source_ship.get("postal"))

    if role_code in _FULL:
        return (
            {"name": name, "phone": phone, "country": country},
            {
                "name": name,
                "phone": phone,
                "country": country,
                "state": state,
                "city": city,
                "line1": line1,
                "postal_code": postal,
            },
        )
    if role_code in _SHIP:
        shown = mask_name_initial(name)
        return (
            {"name": shown, "phone": phone, "country": country},
            {
                "name": shown,
                "phone": phone,
                "country": country,
                "state": state,
                "city": city,
                "line1": line1,
                "postal_code": postal,
            },
        )
    if role_code in _REGION:
        return (
            {"name": None, "phone": None, "country": country},
            {
                "name": None,
                "phone": None,
                "country": country,
                "state": state,
                "city": city,
                "line1": None,
                "postal_code": None,
            },
        )
    if role_code in _CONTACT:
        return (
            {"name": name, "phone": mask_phone_middle(phone), "country": country},
            {
                "name": name,
                "phone": mask_phone_middle(phone),
                "country": country,
                "state": None,
                "city": None,
                "line1": None,
                "postal_code": None,
            },
        )
    return (
        {"name": None, "phone": None, "country": None},
        {
            "name": None,
            "phone": None,
            "country": None,
            "state": None,
            "city": None,
            "line1": None,
            "postal_code": None,
        },
    )


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


__all__ = ["mask_name_initial", "mask_party", "mask_phone_middle", "phone_last4"]
