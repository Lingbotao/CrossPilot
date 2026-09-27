"""M2-07 Webhook：验签、立即受理、事件去重、异步路由。"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from app.adapters.amazon.adapter import AmazonAdapter
from app.adapters.amazon.sns import HttpSnsCertSource, amazon_sns_host_allowed, string_to_sign, verify_sns_signature
from app.adapters.base import CredentialView, WebhookKind
from app.adapters.lazada.adapter import LazadaAdapter
from app.adapters.shopee.adapter import ShopeeAdapter
from app.adapters.signing import lazada_push_sign, shopee_push_sign, signatures_match, tiktok_push_sign
from app.adapters.tiktok.adapter import TikTokAdapter
from app.core.config import settings
from app.core.errors import AppError, ErrorCode, PlatformUnsupportedError
from app.engines.order_status import StatusChangeSource
from app.services.webhook_dispatch import (
    ORDER_EVENT_SOURCE,
    WebhookDispatcher,
    WebhookProcessError,
    confirm_subscription,
)
from app.services.webhook_ingress import WebhookIngress, ip_allowed
from app.sync_engine.errors import StoreUnavailable
from app.sync_engine.queues import task_queue
from app.sync_engine.resilience import DeadLetterQueue, MemoryLetterList
from app.sync_engine.runtime import reset_runtime
from app.webhooks.store import MemoryEventDedup, MemoryWebhookLimiter

_URL = "https://example.com/api/v1/webhooks/shopee"
_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_CERT_URL = "https://sns.us-east-1.amazonaws.com/SimpleNotificationService-test.pem"


def _shopee_body(*, status: str = "READY_TO_SHIP", code: int = 3, order_sn: str = "2601150000001") -> bytes:
    return json.dumps(
        {
            "msg_id": "m-1",
            "shop_id": 600002,
            "code": code,
            "timestamp": 1768467600,
            "data": {"ordersn": order_sn, "status": status, "update_time": 1768467600},
        },
        separators=(",", ":"),
    ).encode()


def _signed_shopee(body: bytes, url: str = _URL) -> dict[str, str]:
    return {"authorization": shopee_push_sign(partner_key="fixture-secret", url=url, body=body)}


def _ingress(**kwargs: object) -> tuple[WebhookIngress, list[dict[str, str]], DeadLetterQueue]:
    published: list[dict[str, str]] = []
    letters = MemoryLetterList()
    queue = DeadLetterQueue(letters)
    ingress = WebhookIngress(
        dedup=kwargs.get("dedup", MemoryEventDedup()),  # type: ignore[arg-type]
        limiter=kwargs.get("limiter", MemoryWebhookLimiter()),  # type: ignore[arg-type]
        publisher=kwargs.get("publisher", published.append),  # type: ignore[arg-type]
        dlq=kwargs.get("dlq", queue),  # type: ignore[arg-type]
        clock=lambda: _NOW,
    )
    return ingress, published, queue


def _cred(platform: str, site: str) -> CredentialView:
    return CredentialView(
        shop_id="1",
        platform=platform,
        site_code=site,
        access_token="fixture-token",
        refresh_token=None,
        expires_at=_NOW,
    )


class _Orders:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def apply(self, *, platform: str, platform_shop_id: str, platform_order_id: str) -> dict[str, object]:
        self.calls.append((platform, platform_shop_id, platform_order_id))
        return {"action": "insert"}


class _DownDedup:
    async def claim(self, key: str, ttl_seconds: int, *, now: datetime) -> bool:
        del key, ttl_seconds, now
        raise StoreUnavailable("down")

    async def release(self, key: str) -> None:
        del key


class _DownLetters:
    async def push(self, item: object) -> None:
        del item
        raise StoreUnavailable("down")

    async def pop(self) -> None:
        return None

    async def size(self) -> int:
        return 0


def _sns_material() -> tuple[bytes, dict[str, object]]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "sns")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(_NOW)
        .not_valid_after(_NOW + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    message: dict[str, object] = {
        "Type": "Notification",
        "MessageId": "mid-1",
        "TopicArn": "arn:aws:sns:us-east-1:123:orders",
        "Message": json.dumps(
            {
                "NotificationType": "ORDER_CHANGE",
                "EventTime": "2026-03-01T12:00:00Z",
                "Payload": {
                    "OrderChangeNotification": {
                        "SellerId": "A3SELLER",
                        "AmazonOrderId": "111-0000000-0000001",
                        "Summary": {"OrderStatus": "Unshipped"},
                    }
                },
                "NotificationMetadata": {"NotificationId": "note-1"},
            }
        ),
        "Timestamp": "2026-03-01T12:00:00.000Z",
        "SigningCertURL": _CERT_URL,
        "SignatureVersion": "1",
    }
    signed = string_to_sign(message)
    assert signed is not None
    signature = key.sign(signed, padding.PKCS1v15(), hashes.SHA1())
    message["Signature"] = base64.b64encode(signature).decode()
    return pem, message


class _Certs:
    def __init__(self, pem: bytes) -> None:
        self.pem = pem
        self.urls: list[str] = []

    async def load(self, url: str) -> bytes | None:
        self.urls.append(url)
        return self.pem


def test_push_signatures_cover_the_documented_base_string() -> None:
    body = b'{"ok":1}'
    shopee = shopee_push_sign(partner_key="k", url="https://cb.example/hook", body=body)
    assert signatures_match(shopee, shopee.upper())
    assert not signatures_match(shopee, shopee_push_sign(partner_key="k", url="https://cb.example/hook", body=b"{}"))
    lazada = lazada_push_sign(app_secret="s", body=body)
    assert lazada != lazada_push_sign(app_secret="s", body=body + b" ")
    tiktok = tiktok_push_sign(app_secret="s", app_key="app", body=body)
    assert tiktok != tiktok_push_sign(app_secret="s", app_key="other", body=body)
    assert not signatures_match("abc", "abcd")


def test_shopee_lazada_and_tiktok_parse_order_events() -> None:
    shopee = ShopeeAdapter().parse_webhook(_shopee_body())
    assert shopee.kind == WebhookKind.ORDER_STATUS_CHANGED.value
    assert shopee.platform_shop_id == "600002"
    assert shopee.platform_order_id == "2601150000001"
    created = ShopeeAdapter().parse_webhook(_shopee_body(status="UNPAID"))
    assert created.kind == WebhookKind.ORDER_CREATED.value
    stock = ShopeeAdapter().parse_webhook(_shopee_body(code=8, status=""))
    assert stock.kind == WebhookKind.INVENTORY_CHANGED.value
    chat = ShopeeAdapter().parse_webhook(_shopee_body(code=10, status=""))
    assert chat.kind == WebhookKind.MESSAGE_CREATED.value

    lazada_body = json.dumps(
        {
            "seller_id": "100",
            "message_type": 0,
            "timestamp": 1768467600,
            "data": {"trade_order_id": "9000000000001", "order_status": "unpaid", "status_update_time": 1768467600},
        }
    ).encode()
    lazada = LazadaAdapter().parse_webhook(lazada_body)
    assert lazada.kind == WebhookKind.ORDER_CREATED.value
    assert lazada.platform_shop_id == "100"

    tiktok_body = json.dumps(
        {
            "type": 1,
            "tts_notification_id": "ntf-1",
            "shop_id": "7494",
            "timestamp": 1768467600,
            "data": {"order_id": "576460000000000001", "order_status": "AWAITING_SHIPMENT", "update_time": 1768467600},
        }
    ).encode()
    tiktok = TikTokAdapter().parse_webhook(tiktok_body)
    assert tiktok.kind == WebhookKind.ORDER_STATUS_CHANGED.value
    assert tiktok.event_id == "tiktok:ntf-1:7494:576460000000000001:AWAITING_SHIPMENT"


@pytest.mark.asyncio
async def test_adapters_verify_their_own_signatures() -> None:
    body = _shopee_body()
    assert await ShopeeAdapter().verify_webhook(body=body, headers=_signed_shopee(body), callback_url=_URL)
    assert not await ShopeeAdapter().verify_webhook(body=body, headers={"authorization": "nope"}, callback_url=_URL)
    lazada_body = b'{"seller_id":"1"}'
    lazada_sign = lazada_push_sign(app_secret="fixture-secret", body=lazada_body)
    assert await LazadaAdapter().verify_webhook(
        body=lazada_body, headers={"Authorization": lazada_sign}, callback_url=_URL
    )
    tiktok_body = b'{"type":1}'
    tiktok_sign = tiktok_push_sign(app_secret="fixture-secret", app_key="fixture-app", body=tiktok_body)
    assert await TikTokAdapter().verify_webhook(
        body=tiktok_body, headers={"authorization": tiktok_sign}, callback_url=""
    )
    assert not await TikTokAdapter().verify_webhook(
        body=tiktok_body + b" ", headers={"authorization": tiktok_sign}, callback_url=""
    )


@pytest.mark.asyncio
async def test_amazon_sns_signature_rejects_bad_host_and_tampering() -> None:
    pem, message = _sns_material()
    assert verify_sns_signature(message, pem)
    tampered = dict(message)
    tampered["MessageId"] = "other"
    assert not verify_sns_signature(tampered, pem)
    assert amazon_sns_host_allowed(_CERT_URL)
    assert not amazon_sns_host_allowed("http://sns.us-east-1.amazonaws.com/cert.pem")
    assert not amazon_sns_host_allowed("https://evil.example/cert.pem")
    assert await HttpSnsCertSource().load("https://evil.example/cert.pem") is None

    certs = _Certs(pem)
    adapter = AmazonAdapter(certs=certs)
    body = json.dumps(message).encode()
    assert await adapter.verify_webhook(body=body, headers={}, callback_url="")
    assert certs.urls == [_CERT_URL]
    forged = dict(message)
    forged["SigningCertURL"] = "https://evil.example/cert.pem"
    assert not await adapter.verify_webhook(body=json.dumps(forged).encode(), headers={}, callback_url="")
    event = adapter.parse_webhook(body)
    assert event.kind == WebhookKind.ORDER_STATUS_CHANGED.value
    assert event.platform_shop_id == "A3SELLER"
    assert event.event_id.startswith("amazon:note-1")
    pending = json.loads(str(message["Message"]))
    pending["Payload"]["OrderChangeNotification"]["Summary"]["OrderStatus"] = "Pending"
    wrapped = dict(message)
    wrapped["Message"] = json.dumps(pending)
    assert adapter.parse_webhook(json.dumps(wrapped).encode()).kind == WebhookKind.ORDER_CREATED.value


@pytest.mark.asyncio
async def test_fetch_order_returns_only_the_requested_id() -> None:
    assert (await ShopeeAdapter().fetch_order(_cred("shopee", "SG"), "2601150000001")) is not None
    assert await ShopeeAdapter().fetch_order(_cred("shopee", "SG"), "missing") is None
    assert (await LazadaAdapter().fetch_order(_cred("lazada", "SG"), "9000000000001")) is not None
    assert (await TikTokAdapter().fetch_order(_cred("tiktok", "US"), "576460000000000001")) is not None
    assert (await AmazonAdapter().fetch_order(_cred("amazon", "US"), "111-0000000-0000001")) is not None


def test_event_dedup_expires_and_rate_limit_is_per_second() -> None:
    dedup = MemoryEventDedup()

    async def _run() -> None:
        assert await dedup.claim("shopee:m-1", 10, now=_NOW)
        assert not await dedup.claim("shopee:m-1", 10, now=_NOW + timedelta(seconds=9))
        assert await dedup.claim("shopee:m-1", 10, now=_NOW + timedelta(seconds=10))
        await dedup.release("shopee:m-1")
        assert await dedup.claim("shopee:m-1", 10, now=_NOW)

    import asyncio

    asyncio.run(_run())
    limiter = MemoryWebhookLimiter()

    async def _limit() -> None:
        assert await limiter.allow("shopee", now=_NOW, qps=1)
        assert not await limiter.allow("shopee", now=_NOW, qps=1)
        assert await limiter.allow("lazada", now=_NOW, qps=1)
        assert await limiter.allow("shopee", now=_NOW + timedelta(seconds=1), qps=1)

    asyncio.run(_limit())


def test_ip_allowlist_is_optional_and_accepts_cidr() -> None:
    assert ip_allowed(None, "")
    assert ip_allowed("203.0.113.10", "")
    assert ip_allowed("203.0.113.10", "203.0.113.0/24, 198.51.100.8")
    assert ip_allowed("198.51.100.8", "203.0.113.0/24, 198.51.100.8")
    assert not ip_allowed("198.51.100.9", "203.0.113.0/24")
    assert not ip_allowed(None, "203.0.113.0/24")
    assert not ip_allowed("203.0.113.10", "not-a-cidr, also-bad")


def test_webhook_status_changes_are_marked_as_webhook() -> None:
    assert ORDER_EVENT_SOURCE is StatusChangeSource.WEBHOOK


@pytest.mark.asyncio
async def test_ingress_accepts_once_and_drops_bad_signatures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "webhook_qps", 100)
    monkeypatch.setattr(settings, "webhook_event_ttl_seconds", 86400)
    monkeypatch.setattr(settings, "webhook_source_cidrs", "")
    ingress, published, queue = _ingress()
    body = _shopee_body()
    headers = _signed_shopee(body)
    first = await ingress.accept(
        platform="shopee", body=body, headers=headers, callback_url=_URL, client_ip="203.0.113.8"
    )
    second = await ingress.accept(
        platform="shopee", body=body, headers=headers, callback_url=_URL, client_ip="203.0.113.8"
    )
    assert first.accepted and not first.duplicate
    assert second.accepted and second.duplicate
    assert len(published) == 1
    assert published[0]["platform"] == "shopee"
    assert published[0]["kind"] == WebhookKind.ORDER_STATUS_CHANGED.value
    assert await queue.size() == 0

    with pytest.raises(AppError) as rejected:
        await ingress.accept(
            platform="shopee",
            body=body,
            headers={"authorization": "bad"},
            callback_url=_URL,
            client_ip="203.0.113.8",
        )
    assert rejected.value.status == 401
    assert int(rejected.value.code) == int(ErrorCode.WEBHOOK_REJECTED)
    assert len(published) == 1


@pytest.mark.asyncio
async def test_ingress_rejects_unknown_source_and_excess_qps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "webhook_qps", 100)
    monkeypatch.setattr(settings, "webhook_source_cidrs", "203.0.113.0/24")
    ingress, published, _queue = _ingress()
    body = _shopee_body()
    headers = _signed_shopee(body)
    with pytest.raises(AppError) as blocked:
        await ingress.accept(platform="shopee", body=body, headers=headers, callback_url=_URL, client_ip="198.51.100.9")
    assert int(blocked.value.code) == int(ErrorCode.WEBHOOK_REJECTED)
    assert published == []

    monkeypatch.setattr(settings, "webhook_qps", 1)
    monkeypatch.setattr(settings, "webhook_source_cidrs", "")
    limited_ingress, limited_published, _limited_queue = _ingress()
    other = _shopee_body(order_sn="other-1")
    first = await limited_ingress.accept(
        platform="shopee",
        body=body,
        headers=headers,
        callback_url=_URL,
        client_ip="203.0.113.10",
    )
    assert first.accepted
    with pytest.raises(AppError) as limited:
        await limited_ingress.accept(
            platform="shopee",
            body=other,
            headers=_signed_shopee(other),
            callback_url=_URL,
            client_ip="203.0.113.10",
        )
    assert limited.value.status == 429
    assert len(limited_published) == 1


@pytest.mark.asyncio
async def test_enqueue_failure_keeps_the_body_in_the_dead_letter_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "webhook_qps", 100)
    monkeypatch.setattr(settings, "webhook_source_cidrs", "")
    body = _shopee_body()
    headers = _signed_shopee(body)

    def _boom(_payload: dict[str, str]) -> None:
        raise RuntimeError("broker down")

    failing, _published, queue = _ingress(publisher=_boom)
    held = await failing.accept(platform="shopee", body=body, headers=headers, callback_url=_URL, client_ip=None)
    assert held.accepted and not held.duplicate
    letter = await queue.replay_next()
    assert letter is not None
    assert letter.kwargs["event_id"]
    assert letter.error == "enqueue_failed"
    again = await failing.accept(platform="shopee", body=body, headers=headers, callback_url=_URL, client_ip=None)
    assert again.duplicate

    down, published_down, _down_queue = _ingress(publisher=_boom, dlq=DeadLetterQueue(_DownLetters()))
    with pytest.raises(AppError) as unavailable:
        await down.accept(platform="shopee", body=body, headers=headers, callback_url=_URL, client_ip=None)
    assert unavailable.value.status == 500
    assert published_down == []


@pytest.mark.asyncio
async def test_ingress_still_enqueues_when_dedup_store_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "webhook_qps", 100)
    monkeypatch.setattr(settings, "webhook_source_cidrs", "")
    ingress, published, _queue = _ingress(dedup=_DownDedup())
    body = _shopee_body()
    receipt = await ingress.accept(
        platform="shopee",
        body=body,
        headers=_signed_shopee(body),
        callback_url=_URL,
        client_ip=None,
    )
    assert receipt.accepted and not receipt.duplicate
    assert len(published) == 1
    with pytest.raises(PlatformUnsupportedError):
        await ingress.accept(platform="ebay", body=body, headers={}, callback_url=_URL, client_ip=None)


@pytest.mark.asyncio
async def test_dispatcher_routes_orders_and_defers_the_rest() -> None:
    orders = _Orders()
    dispatcher = WebhookDispatcher(orders=orders)
    applied = await dispatcher.handle(platform="shopee", body=_shopee_body())
    assert applied["status"] == "applied"
    assert applied["kind"] == WebhookKind.ORDER_STATUS_CHANGED.value
    assert orders.calls == [("shopee", "600002", "2601150000001")]
    deferred = await dispatcher.handle(platform="shopee", body=_shopee_body(code=8, status=""))
    assert deferred == {"status": "deferred", "kind": WebhookKind.INVENTORY_CHANGED.value, "milestone": "M3"}
    messages = await dispatcher.handle(platform="shopee", body=_shopee_body(code=10, status=""))
    assert messages["milestone"] == "M5"
    assert len(orders.calls) == 1
    with pytest.raises(WebhookProcessError) as missing:
        await dispatcher.handle(platform="shopee", body=_shopee_body(order_sn=""))
    assert missing.value.retryable is False
    skipped = await confirm_subscription("https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription")
    assert skipped == {"status": "skipped", "reason": "fixture"}
    rejected = await confirm_subscription("https://evil.example/confirm")
    assert rejected["reason"] == "subscribe_url"


def test_webhook_task_follows_the_platform_queue() -> None:
    assert task_queue("sync.webhook_event", platform="tiktok") == "sync.tiktok"
    assert task_queue("sync.webhook_event", platform=None) == "sync"


def test_http_webhook_returns_200_without_a_user_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app

    reset_runtime()
    published: list[dict[str, str]] = []
    monkeypatch.setattr("app.services.webhook_ingress.publish_webhook", published.append)
    monkeypatch.setattr(settings, "webhook_source_cidrs", "")
    monkeypatch.setattr(settings, "webhook_public_base_url", "")
    body = _shopee_body()
    url = "http://testserver/api/v1/webhooks/shopee"
    with TestClient(app) as client:
        response = client.post("/api/v1/webhooks/shopee", content=body, headers=_signed_shopee(body, url))
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["accepted"] is True
    assert payload["data"]["duplicate"] is False
    assert len(published) == 1
    reset_runtime()
