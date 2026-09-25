"""凭证加密存储与令牌续期。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import CredentialView, TokenBundle
from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError
from app.adapters.registry import adapter_registry
from app.core.config import settings
from app.core.context import tenant_context
from app.core.crypto import get_cipher
from app.core.logging import get_logger
from app.db.session import owner_session_scope, session_scope
from app.models.enums import AuditAction, ShopStatus
from app.models.platform import Shop, ShopCredential
from app.models.tenant import Tenant
from app.repositories.identity import AuditLogRepository
from app.repositories.platform import ShopCredentialRepository, ShopRepository
from app.services.outbox import deliver_notice
from app.services.shop_health import next_refresh_failure

log = get_logger(__name__)


def view_from_row(shop: Shop, row: ShopCredential) -> CredentialView:
    cipher = get_cipher()
    extra = dict(row.extra or {})
    extra["platform_shop_id"] = shop.platform_shop_id
    return CredentialView(
        shop_id=str(shop.id),
        platform=shop.platform_code,
        site_code=shop.site_code,
        access_token=cipher.decrypt(row.access_token_enc),
        refresh_token=cipher.decrypt_optional(row.refresh_token_enc),
        expires_at=row.expires_at,
        extra=extra,
    )


def apply_bundle(row: ShopCredential, bundle: TokenBundle) -> None:
    cipher = get_cipher()
    row.access_token_enc = cipher.encrypt(bundle.access_token)
    row.refresh_token_enc = cipher.encrypt_optional(bundle.refresh_token)
    row.expires_at = bundle.expires_at
    row.refresh_expires_at = bundle.refresh_expires_at
    row.extra = {"platform_shop_id": bundle.platform_shop_id}
    row.refresh_fail_count = 0
    row.last_refresh_error = None
    row.last_refresh_at = datetime.now(UTC)


class CredentialService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shops = ShopRepository(session)
        self.credentials = ShopCredentialRepository(session)
        self.audit = AuditLogRepository(session)

    async def store_new(self, shop: Shop, bundle: TokenBundle) -> ShopCredential:
        cipher = get_cipher()
        row = ShopCredential(
            tenant_id=shop.tenant_id,
            shop_id=shop.id,
            access_token_enc=cipher.encrypt(bundle.access_token),
            refresh_token_enc=cipher.encrypt_optional(bundle.refresh_token),
            expires_at=bundle.expires_at,
            refresh_expires_at=bundle.refresh_expires_at,
            extra={"platform_shop_id": bundle.platform_shop_id},
            refresh_fail_count=0,
        )
        return await self.credentials.add(row)

    async def replace(self, row: ShopCredential, bundle: TokenBundle) -> ShopCredential:
        apply_bundle(row, bundle)
        await self.session.flush()
        return row


async def refresh_expiring_credentials() -> dict[str, int]:
    """系统任务：扫描即将过期的令牌。扫描走 owner，刷新回到租户上下文。"""
    register_builtin_adapters()
    lead = settings.token_refresh_lead_minutes
    async with owner_session_scope() as session:
        result = await session.execute(
            text(
                """
                SELECT c.shop_id, c.tenant_id
                FROM shop_credential AS c
                JOIN shop AS s ON s.id = c.shop_id
                WHERE s.deleted_at IS NULL
                  AND s.status = :active
                  AND c.expires_at <= now() + make_interval(mins => :lead)
                """
            ),
            {"active": int(ShopStatus.ACTIVE), "lead": lead},
        )
        due = [(int(shop_id), int(tenant_id)) for shop_id, tenant_id in result.all()]

    refreshed = 0
    alerted = 0
    failed = 0
    for shop_id, tenant_id in due:
        outcome = await _refresh_one(tenant_id, shop_id)
        refreshed += int(outcome == "refreshed")
        alerted += int(outcome == "alerted")
        failed += int(outcome == "failed")
    return {"refreshed": refreshed, "alerted": alerted, "failed": failed, "due": len(due)}


async def _refresh_one(tenant_id: int, shop_id: int) -> str:
    with tenant_context(tenant_id):
        async with session_scope(tenant_id) as session:
            shops = ShopRepository(session)
            credentials = ShopCredentialRepository(session)
            audit = AuditLogRepository(session)
            shop = await shops.get(shop_id)
            row = await credentials.get_by_shop_id(shop_id)
            if shop is None or row is None or shop.status != int(ShopStatus.ACTIVE):
                return "skipped"
            adapter = adapter_registry.get(shop.platform_code)
            view = view_from_row(shop, row)
            try:
                bundle = await adapter.refresh_token(view)
            except AdapterError as exc:
                count, alert = next_refresh_failure(row.refresh_fail_count, settings.token_refresh_alert_threshold)
                row.refresh_fail_count = count
                row.last_refresh_error = str(exc)[:512]
                shop.last_error = row.last_refresh_error
                if alert:
                    shop.status = int(ShopStatus.AUTH_EXPIRED)
                    tenant = await session.get(Tenant, tenant_id)
                    recipient = tenant.contact_email if tenant and tenant.contact_email else ""
                    if recipient:
                        deliver_notice(
                            kind="token_refresh_alert",
                            recipient=recipient,
                            summary=f"店铺 {shop.shop_name} 令牌连续刷新失败 {count} 次",
                        )
                    await audit.append_action(
                        tenant_id=tenant_id,
                        action=AuditAction.TOKEN_REFRESH_ALERT,
                        resource="shop",
                        resource_id=shop.id,
                        after={"refresh_fail_count": count, "shop_name": shop.shop_name},
                    )
                    log.warning("token_refresh_alert", shop_id=shop.id, failures=count)
                    return "alerted"
                return "failed"
            saved = bundle
            if not saved.platform_shop_id:
                saved = TokenBundle(
                    access_token=bundle.access_token,
                    refresh_token=bundle.refresh_token,
                    expires_at=bundle.expires_at,
                    refresh_expires_at=bundle.refresh_expires_at,
                    platform_shop_id=shop.platform_shop_id,
                    shop_name=shop.shop_name,
                    extra=bundle.extra,
                )
            apply_bundle(row, saved)
            if shop.status == int(ShopStatus.AUTH_EXPIRED):
                shop.status = int(ShopStatus.ACTIVE)
            shop.last_error = None
            return "refreshed"


def refresh_lead() -> timedelta:
    return timedelta(minutes=settings.token_refresh_lead_minutes)


__all__ = [
    "CredentialService",
    "apply_bundle",
    "refresh_expiring_credentials",
    "refresh_lead",
    "view_from_row",
]
