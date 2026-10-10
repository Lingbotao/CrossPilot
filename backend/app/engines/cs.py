"""客服 SLA、回复模板变量和工单状态。纯函数，不访问平台。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

SLA_OK: Final[str] = "OK"
SLA_WARN: Final[str] = "WARN"
SLA_OVERDUE: Final[str] = "OVERDUE"
SLA_LEVELS: Final[tuple[str, ...]] = (SLA_OK, SLA_WARN, SLA_OVERDUE)

MESSAGE_UNREAD: Final[str] = "UNREAD"
MESSAGE_READ: Final[str] = "READ"
MESSAGE_STATUSES: Final[tuple[str, ...]] = (MESSAGE_UNREAD, MESSAGE_READ)

TICKET_OPEN: Final[str] = "OPEN"
TICKET_ASSIGNED: Final[str] = "ASSIGNED"
TICKET_IN_PROGRESS: Final[str] = "IN_PROGRESS"
TICKET_CLOSED: Final[str] = "CLOSED"
TICKET_STATUSES: Final[tuple[str, ...]] = (
    TICKET_OPEN,
    TICKET_ASSIGNED,
    TICKET_IN_PROGRESS,
    TICKET_CLOSED,
)

TICKET_TYPES: Final[tuple[str, ...]] = ("INQUIRY", "RETURN", "REFUND", "OTHER")
TEMPLATE_SCENES: Final[tuple[str, ...]] = ("SHIPPING", "REFUND", "DELAY", "OTHER")
TEMPLATE_VARIABLES: Final[tuple[str, ...]] = ("order_no", "tracking_no", "buyer_name")

_TOKEN: Final[re.Pattern[str]] = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
_LANG: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


class TicketAction(StrEnum):
    ASSIGN = "ASSIGN"
    NOTE = "NOTE"
    CLOSE = "CLOSE"


def sla_level(deadline: datetime, now: datetime, warn: timedelta) -> str:
    """剩余时间不大于预警窗标黄，到点或超过标红。时间必须带时区。"""

    if deadline.tzinfo is None or now.tzinfo is None:
        raise ValueError("SLA 时间必须带时区")
    if warn < timedelta(0):
        raise ValueError("SLA 预警时长不能为负")
    if now >= deadline:
        return SLA_OVERDUE
    if deadline - now <= warn:
        return SLA_WARN
    return SLA_OK


def resolve_deadline(received: datetime, supplied: datetime | None, hours: int) -> datetime:
    """平台给了截止时间就用平台的，否则用配置里的响应小时数。"""

    if received.tzinfo is None:
        raise ValueError("消息时间必须带时区")
    if supplied is not None:
        if supplied.tzinfo is None:
            raise ValueError("SLA 截止时间必须带时区")
        return supplied
    if hours <= 0:
        raise ValueError("SLA 小时数必须为正")
    return received + timedelta(hours=hours)


def render_template(body: str, variables: Mapping[str, str]) -> tuple[str, tuple[str, ...]]:
    """替换订单号、物流单号和买家名。缺值或未知变量保留原样，避免发出空白话术。"""

    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in TEMPLATE_VARIABLES:
            return match.group(0)
        value = variables.get(key, "").strip()
        if not value:
            missing.append(key)
            return match.group(0)
        return value

    return _TOKEN.sub(replace, body), tuple(dict.fromkeys(missing))


def clean_lang(value: str) -> str:
    cleaned = value.strip()
    if not _LANG.fullmatch(cleaned):
        raise ValueError("语言码不合法")
    return cleaned


def next_ticket_status(current: str, action: str) -> str:
    """关闭后不能再分派、跟进或重复关闭。"""

    if current not in TICKET_STATUSES:
        raise ValueError("工单状态不认识")
    if current == TICKET_CLOSED:
        raise ValueError("工单已关闭")
    if action == TicketAction.ASSIGN:
        return TICKET_ASSIGNED
    if action == TicketAction.NOTE:
        if current in {TICKET_OPEN, TICKET_ASSIGNED}:
            return TICKET_IN_PROGRESS
        return current
    if action == TicketAction.CLOSE:
        return TICKET_CLOSED
    raise ValueError("工单动作不认识")


def mark_message_read(current: str) -> str:
    if current not in MESSAGE_STATUSES:
        raise ValueError("消息状态不认识")
    return MESSAGE_READ
