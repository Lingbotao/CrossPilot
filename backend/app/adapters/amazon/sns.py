"""Amazon SNS 入站验签。

SP-API 的 HTTPS 订阅走 SNS，不是应用密钥 HMAC。
SignatureVersion 1 是 SHA1withRSA，2 是 SHA256withRSA。证书只能来自 ``sns.<region>.amazonaws.com``。
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.x509 import load_pem_x509_certificate

from app.adapters.base import WebhookEvent, WebhookKind
from app.adapters.errors import AdapterError, RetryDecision
from app.adapters.webhook_common import coalesce_id, load_object

_NOTIFICATION_FIELDS = ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type")
_CONFIRM_FIELDS = ("Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type")
_CONFIRM_TYPES = frozenset({"SubscriptionConfirmation", "UnsubscribeConfirmation"})
_CREATED_STATUSES = frozenset({"Pending", "PendingAvailability"})


class SnsCertSource(Protocol):
    async def load(self, url: str) -> bytes | None: ...


def amazon_sns_host_allowed(url: str) -> bool:
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        return False
    if not host.startswith("sns."):
        return False
    return host.endswith(".amazonaws.com") or host.endswith(".amazonaws.com.cn")


def string_to_sign(message: dict[str, Any]) -> bytes | None:
    kind = message.get("Type")
    fields: tuple[str, ...]
    if kind == "Notification":
        fields = _NOTIFICATION_FIELDS
    elif kind in _CONFIRM_TYPES:
        fields = _CONFIRM_FIELDS
    else:
        return None
    lines: list[str] = []
    for field in fields:
        if field == "Subject" and "Subject" not in message:
            continue
        value = message.get(field)
        if not isinstance(value, str):
            return None
        lines.append(field)
        lines.append(value)
    return ("\n".join(lines) + "\n").encode()


def verify_sns_signature(message: dict[str, Any], cert_pem: bytes) -> bool:
    signed = string_to_sign(message)
    signature_b64 = message.get("Signature")
    version = str(message.get("SignatureVersion") or "")
    if signed is None or not isinstance(signature_b64, str) or version not in {"1", "2"}:
        return False
    try:
        signature = base64.b64decode(signature_b64, validate=True)
        public_key = load_pem_x509_certificate(cert_pem).public_key()
        if not isinstance(public_key, RSAPublicKey):
            return False
        # SignatureVersion 1 官方规定就是 SHA1withRSA，不能改成 SHA256。
        algorithm = hashes.SHA1() if version == "1" else hashes.SHA256()  # noqa: S303  SNS SignatureVersion 1
        public_key.verify(signature, signed, padding.PKCS1v15(), algorithm)
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


class HttpSnsCertSource:
    """按 URL 缓存 SNS 证书。只接受官方主机，不跟随跳转。"""

    def __init__(self) -> None:
        self._cache: dict[str, bytes] = {}

    async def load(self, url: str) -> bytes | None:
        if not amazon_sns_host_allowed(url):
            return None
        cached = self._cache.get(url)
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.get(url)
        except httpx.HTTPError:
            return None
        if response.status_code != 200 or b"BEGIN CERTIFICATE" not in response.content:
            return None
        self._cache[url] = response.content
        return response.content


def parse_amazon_webhook(body: bytes) -> WebhookEvent:
    payload = load_object(body, platform="amazon")
    kind = str(payload.get("Type") or "")
    if kind in _CONFIRM_TYPES:
        message_id = str(payload.get("MessageId") or "")
        return WebhookEvent(
            platform="amazon",
            event_id=coalesce_id("amazon", kind, message_id),
            kind=WebhookKind.SUBSCRIPTION_CONFIRM.value,
            platform_shop_id="",
            raw={"subscribe_url": str(payload.get("SubscribeURL") or "")},
        )
    inner = _notification_body(payload)
    meta = inner.get("NotificationMetadata")
    notification_id = ""
    if isinstance(meta, dict):
        notification_id = str(meta.get("NotificationId") or "")
    change = _order_change(inner)
    summary = change.get("Summary")
    status = ""
    if isinstance(summary, dict):
        status = str(summary.get("OrderStatus") or "")
    notification_type = str(inner.get("NotificationType") or "")
    seller_id = str(change.get("SellerId") or "")
    order_id = str(change.get("AmazonOrderId") or "")
    return WebhookEvent(
        platform="amazon",
        event_id=coalesce_id("amazon", notification_id or payload.get("MessageId"), order_id, status),
        kind=_amazon_kind(notification_type, status),
        platform_shop_id=seller_id,
        platform_order_id=order_id or None,
        platform_status=status or None,
        occurred_at=_event_time(inner.get("EventTime")),
        raw=inner,
    )


def _notification_body(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("Type") != "Notification":
        return payload
    message = payload.get("Message")
    if not isinstance(message, str):
        raise AdapterError("SNS Message 不是字符串", platform="amazon", decision=RetryDecision.FAIL_FAST)
    try:
        inner = json.loads(message)
    except json.JSONDecodeError as exc:
        raise AdapterError("SNS Message 不是合法 JSON", platform="amazon", decision=RetryDecision.FAIL_FAST) from exc
    if not isinstance(inner, dict):
        raise AdapterError("SNS Message 不是对象", platform="amazon", decision=RetryDecision.FAIL_FAST)
    return inner


def _order_change(inner: dict[str, Any]) -> dict[str, Any]:
    body = inner.get("Payload")
    if not isinstance(body, dict):
        return {}
    change = body.get("OrderChangeNotification")
    return change if isinstance(change, dict) else {}


def _amazon_kind(notification_type: str, status: str) -> str:
    if notification_type == "ORDER_CHANGE":
        if status in _CREATED_STATUSES:
            return WebhookKind.ORDER_CREATED.value
        return WebhookKind.ORDER_STATUS_CHANGED.value
    if notification_type in {"LISTINGS_ITEM_STATUS_CHANGE", "LISTINGS_ITEM_ISSUES_CHANGE"}:
        return WebhookKind.INVENTORY_CHANGED.value
    return WebhookKind.IGNORED.value


def _event_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed


__all__ = [
    "HttpSnsCertSource",
    "SnsCertSource",
    "amazon_sns_host_allowed",
    "parse_amazon_webhook",
    "string_to_sign",
    "verify_sns_signature",
]
