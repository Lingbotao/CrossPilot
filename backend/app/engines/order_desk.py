"""订单审核、异常识别、退货状态。纯函数，不访问数据库。

超时提前量由调用方传入（配置项），这里不写死 4 小时。
金额比较只在同一币种内进行，不做汇率换算。
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final


class ReviewStatus(StrEnum):
    AUTO_PASSED = "AUTO_PASSED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ExceptionKind(StrEnum):
    SKU_UNMATCHED = "SKU_UNMATCHED"
    ADDRESS_INVALID = "ADDRESS_INVALID"
    SHIP_DUE_SOON = "SHIP_DUE_SOON"
    SHIP_OVERDUE = "SHIP_OVERDUE"
    REVIEW_PENDING = "REVIEW_PENDING"


class ReturnStatus(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REFUNDED = "REFUNDED"


class RestockStatus(StrEnum):
    NONE = "NONE"
    DEFERRED = "DEFERRED"


REVIEW_STATUS_SQL: Final[str] = ", ".join(f"'{item.value}'" for item in ReviewStatus)
RETURN_STATUS_SQL: Final[str] = ", ".join(f"'{item.value}'" for item in ReturnStatus)
RESTOCK_STATUS_SQL: Final[str] = ", ".join(f"'{item.value}'" for item in RestockStatus)

_LOCKED_REVIEW: Final[frozenset[str]] = frozenset({ReviewStatus.APPROVED.value, ReviewStatus.REJECTED.value})
_SHIPPABLE_REVIEW: Final[frozenset[str]] = frozenset({ReviewStatus.AUTO_PASSED.value, ReviewStatus.APPROVED.value})


@dataclass(frozen=True, slots=True)
class AmountRule:
    currency: str
    amount_gt: Decimal
    enabled: bool = True


def next_review_status(current: str | None, *, total: Decimal, currency: str, rules: list[AmountRule]) -> str:
    """人工已审的结果不被后续同步覆盖。其余按启用中的金额规则重算。"""

    if current in _LOCKED_REVIEW:
        return current
    if amount_needs_review(total, currency, rules):
        return ReviewStatus.PENDING.value
    return ReviewStatus.AUTO_PASSED.value


def amount_needs_review(total: Decimal, currency: str, rules: list[AmountRule]) -> bool:
    code = currency.strip().upper()
    return any(rule.enabled and rule.currency.upper() == code and total > rule.amount_gt for rule in rules)


def review_blocks_ship(review_status: str) -> str | None:
    if review_status == ReviewStatus.PENDING.value:
        return "订单待人工审核，不能发货"
    if review_status == ReviewStatus.REJECTED.value:
        return "订单审核未通过，不能发货"
    if review_status not in _SHIPPABLE_REVIEW:
        return "订单审核状态不能发货"
    return None


def classify_exceptions(
    *,
    unified_status: str,
    review_status: str,
    ship_to: Mapping[str, object] | None,
    sku_unmatched: bool,
    paid_at: datetime | None,
    now: datetime,
    sla_hours: int,
    warn_hours: int,
) -> list[str]:
    found: list[str] = []
    if sku_unmatched:
        found.append(ExceptionKind.SKU_UNMATCHED.value)
    if address_invalid(ship_to):
        found.append(ExceptionKind.ADDRESS_INVALID.value)
    window = ship_window(
        unified_status=unified_status,
        paid_at=paid_at,
        now=now,
        sla_hours=sla_hours,
        warn_hours=warn_hours,
    )
    if window is not None:
        found.append(window)
    if review_status == ReviewStatus.PENDING.value:
        found.append(ExceptionKind.REVIEW_PENDING.value)
    return found


def address_invalid(ship_to: Mapping[str, object] | None) -> bool:
    if not ship_to:
        return True
    country = _text(ship_to.get("country"))
    line1 = _text(ship_to.get("line1"))
    city = _text(ship_to.get("city"))
    return not country or not (line1 or city)


def ship_deadline(paid_at: datetime | None, sla_hours: int) -> datetime | None:
    if paid_at is None:
        return None
    return paid_at + timedelta(hours=sla_hours)


def ship_window(
    *,
    unified_status: str,
    paid_at: datetime | None,
    now: datetime,
    sla_hours: int,
    warn_hours: int,
) -> str | None:
    """只对待发货订单计时。已发货不再算超时。"""

    if unified_status != "PAID" or paid_at is None:
        return None
    deadline = ship_deadline(paid_at, sla_hours)
    if deadline is None:
        return None
    if now >= deadline:
        return ExceptionKind.SHIP_OVERDUE.value
    warn = timedelta(hours=max(warn_hours, 0))
    if now >= deadline - warn:
        return ExceptionKind.SHIP_DUE_SOON.value
    return None


def decide_return(current: str, action: str) -> str | None:
    """返回下一状态。不允许的动作返回 None。"""

    allowed = {
        (ReturnStatus.REQUESTED.value, "approve"): ReturnStatus.APPROVED.value,
        (ReturnStatus.REQUESTED.value, "reject"): ReturnStatus.REJECTED.value,
        (ReturnStatus.APPROVED.value, "refund"): ReturnStatus.REFUNDED.value,
    }
    return allowed.get((current, action))


def restock_after_refund(*, restock_flag: bool) -> str:
    """库存台账在库存模块。这里只记下是否要入库，以及是否计入可售。"""

    if restock_flag:
        return RestockStatus.DEFERRED.value
    return RestockStatus.NONE.value


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


__all__ = [
    "RESTOCK_STATUS_SQL",
    "RETURN_STATUS_SQL",
    "REVIEW_STATUS_SQL",
    "AmountRule",
    "ExceptionKind",
    "RestockStatus",
    "ReturnStatus",
    "ReviewStatus",
    "address_invalid",
    "amount_needs_review",
    "classify_exceptions",
    "decide_return",
    "next_review_status",
    "restock_after_refund",
    "review_blocks_ship",
    "ship_deadline",
    "ship_window",
]
