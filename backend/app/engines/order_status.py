"""统一订单状态机（PRD 5.2 / F4-03）。

九态与 PRD 一致。平台原文留在 ``platform_status``，这里只判定统一状态能不能变。
轮询和 Webhook 允许沿主链向前跳步（中间态可能没拉到）；人工操作只能走相邻边。
未知平台状态不会默认成「已付款」：新单落在 ``PENDING``，并在日志里标明尚未配置映射。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class UnifiedStatus(StrEnum):
    """统一状态。取值即落库文案，对应 PRD 的 1–9。"""

    PENDING = "PENDING"
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REFUNDING = "REFUNDING"
    REFUNDED = "REFUNDED"
    RETURNED = "RETURNED"


class StatusChangeSource(StrEnum):
    """状态从哪来。F4-13：轮询、Webhook、人工。"""

    SYSTEM = "SYSTEM"
    WEBHOOK = "WEBHOOK"
    MANUAL = "MANUAL"


class DecisionKind(StrEnum):
    APPLY = "apply"
    UNCHANGED = "unchanged"
    REJECT = "reject"


UNIFIED_STATUS_CODES: Final[frozenset[str]] = frozenset(status.value for status in UnifiedStatus)
UNIFIED_STATUS_SQL: Final[str] = ", ".join(f"'{status.value}'" for status in UnifiedStatus)
UNMAPPED_REMARK: Final[str] = "平台状态尚未配置映射"

_MAIN: Final[tuple[UnifiedStatus, ...]] = (
    UnifiedStatus.PENDING,
    UnifiedStatus.PAID,
    UnifiedStatus.SHIPPED,
    UnifiedStatus.DELIVERED,
    UnifiedStatus.COMPLETED,
)
_RANK: Final[dict[UnifiedStatus, int]] = {status: index for index, status in enumerate(_MAIN)}

_MANUAL: Final[dict[UnifiedStatus, frozenset[UnifiedStatus]]] = {
    UnifiedStatus.PENDING: frozenset({UnifiedStatus.PAID, UnifiedStatus.CANCELLED}),
    UnifiedStatus.PAID: frozenset({UnifiedStatus.SHIPPED, UnifiedStatus.CANCELLED, UnifiedStatus.REFUNDING}),
    UnifiedStatus.SHIPPED: frozenset(
        {
            UnifiedStatus.DELIVERED,
            UnifiedStatus.CANCELLED,
            UnifiedStatus.REFUNDING,
            UnifiedStatus.RETURNED,
        }
    ),
    UnifiedStatus.DELIVERED: frozenset({UnifiedStatus.COMPLETED, UnifiedStatus.REFUNDING, UnifiedStatus.RETURNED}),
    UnifiedStatus.COMPLETED: frozenset({UnifiedStatus.REFUNDING, UnifiedStatus.RETURNED}),
    UnifiedStatus.REFUNDING: frozenset({UnifiedStatus.REFUNDED, UnifiedStatus.RETURNED}),
    UnifiedStatus.RETURNED: frozenset({UnifiedStatus.REFUNDED}),
    UnifiedStatus.REFUNDED: frozenset(),
    UnifiedStatus.CANCELLED: frozenset(),
}

_CANCEL_FROM: Final[frozenset[UnifiedStatus]] = frozenset(
    {UnifiedStatus.PENDING, UnifiedStatus.PAID, UnifiedStatus.SHIPPED}
)
_REFUND_FROM: Final[frozenset[UnifiedStatus]] = frozenset(
    {UnifiedStatus.PAID, UnifiedStatus.SHIPPED, UnifiedStatus.DELIVERED, UnifiedStatus.COMPLETED}
)
_RETURN_FROM: Final[frozenset[UnifiedStatus]] = frozenset(
    {UnifiedStatus.SHIPPED, UnifiedStatus.DELIVERED, UnifiedStatus.COMPLETED, UnifiedStatus.REFUNDING}
)


@dataclass(frozen=True, slots=True)
class StatusDecision:
    kind: DecisionKind
    from_status: UnifiedStatus | None
    to_status: UnifiedStatus
    unmapped: bool


def parse_unified(value: str | None) -> UnifiedStatus | None:
    if not value:
        return None
    try:
        return UnifiedStatus(value)
    except ValueError:
        return None


def resolve_target(platform_status: str, table: dict[str, str]) -> UnifiedStatus | None:
    """映射表命中且落在九态内才算数。未配置返回 None，调用方不得自行猜成 PAID。"""

    return parse_unified(table.get(platform_status))


def merge_mapping(defaults: dict[str, str], stored: dict[str, str]) -> dict[str, str]:
    """代码里的默认映射补缺，库里的配置覆盖同名平台状态。非法统一状态丢弃。"""

    merged = {key: value for key, value in defaults.items() if parse_unified(value) is not None}
    for key, value in stored.items():
        parsed = parse_unified(value)
        if parsed is not None:
            merged[key] = parsed.value
    return merged


def decide_status(
    current: UnifiedStatus | None,
    target: UnifiedStatus | None,
    *,
    source: StatusChangeSource,
) -> StatusDecision:
    if target is None:
        if current is None:
            return StatusDecision(DecisionKind.APPLY, None, UnifiedStatus.PENDING, True)
        return StatusDecision(DecisionKind.UNCHANGED, current, current, True)
    if current is None:
        return StatusDecision(DecisionKind.APPLY, None, target, False)
    if current is target:
        return StatusDecision(DecisionKind.UNCHANGED, current, current, False)
    if _allowed(current, target, source):
        return StatusDecision(DecisionKind.APPLY, current, target, False)
    return StatusDecision(DecisionKind.REJECT, current, current, False)


def _allowed(current: UnifiedStatus, target: UnifiedStatus, source: StatusChangeSource) -> bool:
    if source is StatusChangeSource.MANUAL:
        return target in _MANUAL[current]
    if current is UnifiedStatus.CANCELLED:
        return False
    if current is UnifiedStatus.REFUNDED:
        return target is UnifiedStatus.RETURNED
    if current is UnifiedStatus.RETURNED:
        return target is UnifiedStatus.REFUNDED
    if current in _RANK and target in _RANK:
        return _RANK[target] > _RANK[current]
    if target is UnifiedStatus.CANCELLED:
        return current in _CANCEL_FROM
    if target is UnifiedStatus.REFUNDING:
        return current in _REFUND_FROM
    if target is UnifiedStatus.REFUNDED:
        return current in _REFUND_FROM or current is UnifiedStatus.REFUNDING
    if target is UnifiedStatus.RETURNED:
        return current in _RETURN_FROM
    return False


__all__ = [
    "UNIFIED_STATUS_CODES",
    "UNIFIED_STATUS_SQL",
    "UNMAPPED_REMARK",
    "DecisionKind",
    "StatusChangeSource",
    "StatusDecision",
    "UnifiedStatus",
    "decide_status",
    "merge_mapping",
    "parse_unified",
    "resolve_target",
]
