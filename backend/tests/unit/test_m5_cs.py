"""客服 SLA、模板变量和工单状态。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.errors import AdapterError
from app.adapters.messages import parse_amazon_messages
from app.engines.cs import (
    SLA_OK,
    SLA_OVERDUE,
    SLA_WARN,
    TICKET_ASSIGNED,
    TICKET_CLOSED,
    TICKET_IN_PROGRESS,
    TICKET_OPEN,
    TicketAction,
    clean_lang,
    mark_message_read,
    next_ticket_status,
    render_template,
    resolve_deadline,
    sla_level,
)


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 10, hour, minute, tzinfo=UTC)


def test_sla_warns_inside_the_window_and_flags_overtime() -> None:
    deadline = _at(12)
    warn = timedelta(hours=2)
    assert sla_level(deadline, _at(9), warn) == SLA_OK
    assert sla_level(deadline, _at(10), warn) == SLA_WARN
    assert sla_level(deadline, _at(11, 30), warn) == SLA_WARN
    assert sla_level(deadline, deadline, warn) == SLA_OVERDUE
    assert sla_level(deadline, _at(13), warn) == SLA_OVERDUE


def test_sla_rejects_naive_clocks() -> None:
    with pytest.raises(ValueError, match="时区"):
        sla_level(datetime(2026, 10, 10, 12, 0), _at(10), timedelta(hours=2))


def test_deadline_uses_platform_value_or_configured_hours() -> None:
    received = _at(8)
    supplied = _at(9)
    assert resolve_deadline(received, supplied, 24) == supplied
    assert resolve_deadline(received, None, 24) == _at(8) + timedelta(hours=24)
    with pytest.raises(ValueError, match="为正"):
        resolve_deadline(received, None, 0)


def test_template_keeps_missing_and_unknown_tokens() -> None:
    body = "订单 {{order_no}}，物流 {{ tracking_no }}，{{buyer_name}}，{{shop}}"
    rendered, missing = render_template(body, {"order_no": "111-1", "tracking_no": "  ", "buyer_name": "Ann"})
    assert rendered == "订单 111-1，物流 {{ tracking_no }}，Ann，{{shop}}"
    assert missing == ("tracking_no",)


def test_lang_code_and_message_read() -> None:
    assert clean_lang(" zh-CN ") == "zh-CN"
    with pytest.raises(ValueError, match="语言"):
        clean_lang("中文")
    assert mark_message_read("UNREAD") == "READ"
    assert mark_message_read("READ") == "READ"


def test_amazon_message_rejects_a_non_https_console() -> None:
    with pytest.raises(AdapterError, match="https"):
        parse_amazon_messages(
            {
                "messages": [
                    {
                        "messageId": "m1",
                        "body": "hi",
                        "createdDate": "2026-10-10T01:00:00Z",
                        "consoleUrl": "http://sellercentral.amazon.com/messaging",
                    }
                ]
            }
        )


def test_ticket_moves_forward_and_stops_when_closed() -> None:
    assert next_ticket_status(TICKET_OPEN, TicketAction.ASSIGN) == TICKET_ASSIGNED
    assert next_ticket_status(TICKET_ASSIGNED, TicketAction.NOTE) == TICKET_IN_PROGRESS
    assert next_ticket_status(TICKET_IN_PROGRESS, TicketAction.NOTE) == TICKET_IN_PROGRESS
    assert next_ticket_status(TICKET_IN_PROGRESS, TicketAction.CLOSE) == TICKET_CLOSED
    with pytest.raises(ValueError, match="已关闭"):
        next_ticket_status(TICKET_CLOSED, TicketAction.NOTE)
