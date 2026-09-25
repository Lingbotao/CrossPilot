"""开发交付版邮件 outbox。

本轮不接 SMTP。测试可读取内存 outbox；非生产环境同时记录可点击链接。
生产环境绝不把令牌写日志，后续接真实通知适配器后替换本模块。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    kind: str
    recipient: str
    url: str
    summary: str = ""


_messages: list[OutboxMessage] = []


def deliver_link(*, kind: str, recipient: str, path: str, token: str) -> None:
    url = f"{settings.frontend_base_url.rstrip('/')}{path}?token={token}"
    message = OutboxMessage(kind=kind, recipient=recipient, url=url)
    _messages.append(message)
    if settings.is_prod:
        log.warning("email_outbox_not_configured", kind=kind, recipient=recipient)
    else:
        log.info("email_outbox_delivered", kind=kind, recipient=recipient, url=url)


def deliver_notice(*, kind: str, recipient: str, summary: str) -> None:
    """开发环境告警出口。生产环境只记种类，不把令牌写进日志。"""
    message = OutboxMessage(kind=kind, recipient=recipient, url="", summary=summary)
    _messages.append(message)
    log.info("notice_outbox_delivered", kind=kind, recipient=recipient)


def messages() -> tuple[OutboxMessage, ...]:
    return tuple(_messages)


def reset_outbox() -> None:
    _messages.clear()


__all__ = ["OutboxMessage", "deliver_link", "deliver_notice", "messages", "reset_outbox"]
