"""合规窗口与税率比例换算。

日数是 F9-04 / F9-06 的提醒窗口，不是税率。
百分数换成小数比例只用进位单位，不写任何法定税率。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.core.errors import ParamInvalidError

_HUNDRED = Decimal("100")
_SCALE = Decimal("0.000001")
_MAX_FRACTION = Decimal("10")
TAX_LEAD_DAYS = 7
CERT_SOON_DAYS = 7
CERT_MID_DAYS = 30
CERT_FAR_DAYS = 60
GAP_MISSING = "MISSING"
GAP_EXPIRED = "EXPIRED"


def percent_to_fraction(percent: Decimal) -> Decimal:
    """把录入的百分数换成库存比例。9 变成 0.090000。"""

    if percent < 0:
        raise ParamInvalidError("税率不能为负")
    fraction = (percent / _HUNDRED).quantize(_SCALE, rounding=ROUND_HALF_UP)
    if fraction > _MAX_FRACTION:
        raise ParamInvalidError("税率超出允许范围")
    return fraction


def format_fraction(value: Decimal) -> str:
    return f"{value.quantize(_SCALE, rounding=ROUND_HALF_UP):.6f}"


def fraction_to_percent(value: Decimal) -> str:
    percent = (value * _HUNDRED).quantize(_SCALE, rounding=ROUND_HALF_UP)
    return f"{percent:.6f}"


def ranges_overlap(start_a: date, end_a: date | None, start_b: date, end_b: date | None) -> bool:
    """半开区间 [start, end) 是否重叠。结束日为空表示尚未结束。"""

    a_reaches_b = end_b is None or start_a < end_b
    b_reaches_a = end_a is None or start_b < end_a
    return a_reaches_b and b_reaches_a


def tax_window(effective_from: date, today: date) -> str | None:
    """生效日在今天起的提醒窗口内时返回 D7。"""

    days = (effective_from - today).days
    if 0 <= days <= TAX_LEAD_DAYS:
        return "D7"
    return None


def cert_window(expires_on: date, today: date) -> str | None:
    """到期日落入 60/30/7 或已经过期。区间按整天计算。"""

    days = (expires_on - today).days
    if days < 0:
        return "EXPIRED"
    if days <= CERT_SOON_DAYS:
        return "D7"
    if days <= CERT_MID_DAYS:
        return "D30"
    if days <= CERT_FAR_DAYS:
        return "D60"
    return None


def tax_horizon(today: date) -> date:
    return today + timedelta(days=TAX_LEAD_DAYS)


def cert_horizon(today: date) -> date:
    return today + timedelta(days=CERT_FAR_DAYS)


def cert_gaps(required: set[str], held_until: dict[str, date], today: date) -> list[tuple[str, str]]:
    """有效证书的到期日不早于今天。过期和缺失都算缺口。"""

    gaps: list[tuple[str, str]] = []
    for cert_type in sorted(required):
        expires_on = held_until.get(cert_type)
        if expires_on is None:
            gaps.append((cert_type, GAP_MISSING))
        elif expires_on < today:
            gaps.append((cert_type, GAP_EXPIRED))
    return gaps


__all__ = [
    "CERT_FAR_DAYS",
    "CERT_MID_DAYS",
    "CERT_SOON_DAYS",
    "GAP_EXPIRED",
    "GAP_MISSING",
    "TAX_LEAD_DAYS",
    "cert_gaps",
    "cert_horizon",
    "cert_window",
    "format_fraction",
    "fraction_to_percent",
    "percent_to_fraction",
    "ranges_overlap",
    "tax_horizon",
    "tax_window",
]
