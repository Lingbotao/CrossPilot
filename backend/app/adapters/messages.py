"""把各平台的客服报文收成统一消息。字段名留在本文件的平台函数里。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.adapters.base import PageResult, UnifiedMessage
from app.adapters.errors import AdapterError, RetryDecision

_MAX_CONTENT = 8000


def _fail(platform: str, message: str) -> AdapterError:
    return AdapterError(message, platform=platform, decision=RetryDecision.FAIL_FAST)


def _text(value: object, *, platform: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(platform, f"客服字段 {field} 缺失")
    return value.strip()


def _optional(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _time(value: object, *, platform: str, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise _fail(platform, f"客服字段 {field} 缺失")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fail(platform, f"客服字段 {field} 不是时间") from exc
    if parsed.tzinfo is None:
        raise _fail(platform, f"客服字段 {field} 必须带时区")
    return parsed.astimezone(UTC)


def _optional_time(value: object, *, platform: str, field: str) -> datetime | None:
    if value is None or value == "":
        return None
    return _time(value, platform=platform, field=field)


def _message(
    *,
    platform: str,
    platform_message_id: str,
    platform_order_id: str | None,
    buyer_id: str | None,
    buyer_name: str | None,
    content: str,
    lang: str | None,
    received_at: datetime,
    sla_deadline: datetime | None,
    console_url: str,
) -> UnifiedMessage:
    if len(content) > _MAX_CONTENT:
        raise _fail(platform, "客服消息过长")
    if not console_url.startswith("https://"):
        raise _fail(platform, "客服跳转地址必须是 https")
    return UnifiedMessage(
        platform_message_id=platform_message_id,
        platform_order_id=platform_order_id,
        buyer_id=buyer_id,
        buyer_name=buyer_name,
        content=content,
        lang=lang,
        received_at=received_at,
        sla_deadline=sla_deadline,
        console_url=console_url,
    )


def _rows(raw: dict[str, Any], key: str, *, platform: str) -> list[dict[str, Any]]:
    rows = raw.get(key)
    if not isinstance(rows, list):
        raise _fail(platform, "客服报文缺少消息列表")
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise _fail(platform, "客服消息不是对象")
        items.append(row)
    return items


def parse_amazon_messages(raw: dict[str, Any]) -> PageResult[UnifiedMessage]:
    items = [
        _message(
            platform="amazon",
            platform_message_id=_text(row.get("messageId"), platform="amazon", field="messageId"),
            platform_order_id=_optional(row.get("amazonOrderId")),
            buyer_id=_optional(row.get("buyerId")),
            buyer_name=_optional(row.get("buyerName")),
            content=_text(row.get("body"), platform="amazon", field="body"),
            lang=_optional(row.get("language")),
            received_at=_time(row.get("createdDate"), platform="amazon", field="createdDate"),
            sla_deadline=_optional_time(row.get("responseDeadline"), platform="amazon", field="responseDeadline"),
            console_url=_text(row.get("consoleUrl"), platform="amazon", field="consoleUrl"),
        )
        for row in _rows(raw, "messages", platform="amazon")
    ]
    return PageResult(items=items, next_cursor=_optional(raw.get("nextCursor")))


def parse_shopee_messages(raw: dict[str, Any]) -> PageResult[UnifiedMessage]:
    items = [
        _message(
            platform="shopee",
            platform_message_id=_text(row.get("message_id"), platform="shopee", field="message_id"),
            platform_order_id=_optional(row.get("order_sn")),
            buyer_id=_optional(row.get("from_id")),
            buyer_name=_optional(row.get("from_name")),
            content=_text(row.get("message_content"), platform="shopee", field="message_content"),
            lang=_optional(row.get("language")),
            received_at=_time(row.get("create_time"), platform="shopee", field="create_time"),
            sla_deadline=_optional_time(row.get("reply_deadline"), platform="shopee", field="reply_deadline"),
            console_url=_text(row.get("console_url"), platform="shopee", field="console_url"),
        )
        for row in _rows(raw, "message_list", platform="shopee")
    ]
    return PageResult(items=items, next_cursor=_optional(raw.get("next_cursor")))


def parse_lazada_messages(raw: dict[str, Any]) -> PageResult[UnifiedMessage]:
    items = [
        _message(
            platform="lazada",
            platform_message_id=_text(row.get("message_id"), platform="lazada", field="message_id"),
            platform_order_id=_optional(row.get("order_id")),
            buyer_id=_optional(row.get("from_account_id")),
            buyer_name=_optional(row.get("from_account_name")),
            content=_text(row.get("content"), platform="lazada", field="content"),
            lang=_optional(row.get("lang")),
            received_at=_time(row.get("send_time"), platform="lazada", field="send_time"),
            sla_deadline=_optional_time(row.get("sla_deadline"), platform="lazada", field="sla_deadline"),
            console_url=_text(row.get("console_url"), platform="lazada", field="console_url"),
        )
        for row in _rows(raw, "messages", platform="lazada")
    ]
    return PageResult(items=items, next_cursor=_optional(raw.get("next_page")))


def parse_tiktok_messages(raw: dict[str, Any]) -> PageResult[UnifiedMessage]:
    items = [
        _message(
            platform="tiktok",
            platform_message_id=_text(row.get("message_id"), platform="tiktok", field="message_id"),
            platform_order_id=_optional(row.get("order_id")),
            buyer_id=_optional(row.get("buyer_user_id")),
            buyer_name=_optional(row.get("buyer_nickname")),
            content=_text(row.get("content"), platform="tiktok", field="content"),
            lang=_optional(row.get("language")),
            received_at=_time(row.get("create_time"), platform="tiktok", field="create_time"),
            sla_deadline=_optional_time(row.get("sla_deadline"), platform="tiktok", field="sla_deadline"),
            console_url=_text(row.get("console_url"), platform="tiktok", field="console_url"),
        )
        for row in _rows(raw, "messages", platform="tiktok")
    ]
    return PageResult(items=items, next_cursor=_optional(raw.get("next_page_token")))
