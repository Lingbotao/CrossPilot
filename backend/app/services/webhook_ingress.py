"""平台 Webhook 入站（M2-07 / PRD 10.5）。

请求里只做：限流、来源地址、验签、事件去重、入队。
订单入库和拉详情在 Celery 里做。验签失败只记哈希和来源 IP，不落原始报文。
"""

from __future__ import annotations

import hashlib
import ipaddress
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError
from app.adapters.registry import SUPPORTED_PLATFORMS, adapter_registry
from app.core.config import settings
from app.core.errors import AppError, ErrorCode, PlatformUnsupportedError
from app.core.logging import get_logger
from app.schemas.webhook import WebhookAcceptResponse
from app.sync_engine.errors import StoreUnavailable
from app.sync_engine.resilience import DeadLetter, DeadLetterQueue, make_dead_letter
from app.sync_engine.runtime import get_dead_letter_queue, get_webhook_dedup, get_webhook_limiter
from app.webhooks.store import EventDedup, WebhookLimiter

log = get_logger(__name__)

Publisher = Callable[[dict[str, str]], None]


@dataclass(frozen=True, slots=True)
class _Held:
    accepted: bool
    duplicate: bool


def publish_webhook(payload: dict[str, str]) -> None:
    from app.tasks.sync import webhook_event

    webhook_event.apply_async(kwargs=dict(payload))


def ip_allowed(client: str | None, raw_cidrs: str) -> bool:
    rules = [piece.strip() for piece in raw_cidrs.split(",") if piece.strip()]
    if not rules:
        return True
    if not client:
        return False
    try:
        address = ipaddress.ip_address(client)
    except ValueError:
        return False
    for rule in rules:
        try:
            if "/" in rule:
                if address in ipaddress.ip_network(rule, strict=False):
                    return True
            elif address == ipaddress.ip_address(rule):
                return True
        except ValueError:
            continue
    return False


def callback_url_for(*, request_url: str, platform: str) -> str:
    base = settings.webhook_public_base_url.strip().rstrip("/")
    if base:
        return f"{base}{settings.api_v1_prefix}/webhooks/{platform}"
    return request_url.split("?", 1)[0]


class WebhookIngress:
    def __init__(
        self,
        *,
        dedup: EventDedup | None = None,
        limiter: WebhookLimiter | None = None,
        publisher: Publisher | None = None,
        dlq: DeadLetterQueue | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._dedup = dedup
        self._limiter = limiter
        self._publisher = publisher or publish_webhook
        self._dlq = dlq
        self._clock = clock or (lambda: datetime.now(UTC))
        register_builtin_adapters()

    async def accept(
        self,
        *,
        platform: str,
        body: bytes,
        headers: Mapping[str, str],
        callback_url: str,
        client_ip: str | None,
    ) -> WebhookAcceptResponse:
        code = platform.strip().lower()
        if code not in SUPPORTED_PLATFORMS:
            raise PlatformUnsupportedError()
        if len(body) > settings.webhook_max_body_bytes:
            raise AppError("Webhook 报文过大", code=ErrorCode.PARAM_INVALID)
        now = self._clock()
        if not await self._allowed(code, now):
            raise AppError("Webhook 请求过于频繁", code=ErrorCode.PLATFORM_RATE_LIMITED)
        if not ip_allowed(client_ip, settings.webhook_source_cidrs):
            log.warning("webhook_source_rejected", platform=code, client_ip=client_ip, body_sha256=_fingerprint(body))
            raise AppError("Webhook 来源未授权", code=ErrorCode.WEBHOOK_REJECTED)
        adapter = adapter_registry.get(code)
        if not await adapter.verify_webhook(body=body, headers=headers, callback_url=callback_url):
            log.warning(
                "webhook_signature_rejected", platform=code, client_ip=client_ip, body_sha256=_fingerprint(body)
            )
            raise AppError("Webhook 签名校验失败", code=ErrorCode.WEBHOOK_REJECTED)
        try:
            event = adapter.parse_webhook(body)
        except AdapterError:
            log.warning("webhook_parse_failed", platform=code, client_ip=client_ip, body_sha256=_fingerprint(body))
            held = await self._retain(code, body, event_id=_fingerprint(body), kind="ignored", error="parse_failed")
            return WebhookAcceptResponse(accepted=held.accepted, duplicate=held.duplicate)
        payload = {
            "platform": code,
            "body": _text(body),
            "event_id": event.event_id,
            "kind": event.kind,
        }
        try:
            claimed = await self._claim(f"{code}:{event.event_id}", now)
        except StoreUnavailable:
            log.warning("webhook_dedup_unavailable", platform=code, event_id=event.event_id)
            claimed = True
        if not claimed:
            log.info("webhook_duplicate", platform=code, event_id=event.event_id)
            return WebhookAcceptResponse(accepted=True, duplicate=True)
        try:
            self._publisher(payload)
        except Exception as exc:
            log.warning("webhook_enqueue_failed", platform=code, event_id=event.event_id, error=str(exc)[:200])
            saved = await self._push_letter(
                code, body, event_id=event.event_id, kind=event.kind, error="enqueue_failed"
            )
            if not saved:
                await self._release(f"{code}:{event.event_id}")
                raise AppError("Webhook 入队失败", code=ErrorCode.INTERNAL_ERROR) from exc
            return WebhookAcceptResponse(accepted=True, duplicate=False)
        log.info("webhook_accepted", platform=code, event_id=event.event_id, kind=event.kind)
        return WebhookAcceptResponse(accepted=True, duplicate=False)

    async def _allowed(self, platform: str, now: datetime) -> bool:
        limiter = self._limiter if self._limiter is not None else get_webhook_limiter()
        try:
            return await limiter.allow(platform, now=now, qps=settings.webhook_qps)
        except StoreUnavailable:
            log.warning("webhook_limiter_unavailable", platform=platform)
            return True

    async def _claim(self, key: str, now: datetime) -> bool:
        dedup = self._dedup if self._dedup is not None else get_webhook_dedup()
        return await dedup.claim(key, settings.webhook_event_ttl_seconds, now=now)

    async def _release(self, key: str) -> None:
        dedup = self._dedup if self._dedup is not None else get_webhook_dedup()
        try:
            await dedup.release(key)
        except StoreUnavailable:
            log.warning("webhook_dedup_release_failed", key=key)

    async def _retain(self, platform: str, body: bytes, *, event_id: str, kind: str, error: str) -> _Held:
        now = self._clock()
        try:
            claimed = await self._claim(f"{platform}:{event_id}", now)
        except StoreUnavailable:
            claimed = True
        if not claimed:
            return _Held(accepted=True, duplicate=True)
        saved = await self._push_letter(platform, body, event_id=event_id, kind=kind, error=error)
        if saved:
            return _Held(accepted=True, duplicate=False)
        await self._release(f"{platform}:{event_id}")
        raise AppError("Webhook 入队失败", code=ErrorCode.INTERNAL_ERROR)

    async def _push_letter(self, platform: str, body: bytes, *, event_id: str, kind: str, error: str) -> bool:
        letter = make_dead_letter(
            task_name="sync.webhook_event",
            tenant_id=0,
            kwargs={
                "platform": platform,
                "body": _text(body),
                "event_id": event_id,
                "kind": kind,
            },
            error=error,
        )
        queue = self._dlq if self._dlq is not None else get_dead_letter_queue()
        try:
            await queue.push(letter)
        except StoreUnavailable:
            log.warning("webhook_dlq_unavailable", platform=platform, event_id=event_id)
            return False
        return True


def dead_letter_for_task(
    *, platform: str, body: str, event_id: str, kind: str, error: str, tenant_id: int
) -> DeadLetter:
    return make_dead_letter(
        task_name="sync.webhook_event",
        tenant_id=tenant_id,
        kwargs={"platform": platform, "body": body, "event_id": event_id, "kind": kind},
        error=error,
    )


def _fingerprint(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _text(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return _fingerprint(body)


__all__ = [
    "WebhookIngress",
    "callback_url_for",
    "dead_letter_for_task",
    "ip_allowed",
    "publish_webhook",
]
