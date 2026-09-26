"""店铺授权、解绑与手动同步。"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import CredentialView, PlatformAdapter, UnifiedOrder
from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError
from app.adapters.registry import PLATFORM_DISPLAY_NAMES, adapter_registry
from app.adapters.sites import site_supported, supported_sites
from app.adapters.transport import use_fixture_transport
from app.core.config import settings
from app.core.context import get_trace_id
from app.core.errors import AppError, ErrorCode, ParamInvalidError, PlatformUnsupportedError
from app.core.pagination import PageData, build_cursor_page, build_page, decode_cursor
from app.core.security import create_purpose_token, decode_purpose_token
from app.models.enums import AuditAction, ShopStatus, SyncStatus, SyncTrigger
from app.models.platform import PlatformApiLog, Shop, ShopCredential, SyncTask
from app.models.tenant import SysUser, Tenant
from app.repositories.identity import AuditLogRepository
from app.repositories.platform import (
    PlatformApiLogRepository,
    ShopCredentialRepository,
    ShopRepository,
    SyncTaskRepository,
)
from app.schemas.common import money_to_str
from app.schemas.shop import (
    AuthUrlResponse,
    PlatformSiteCatalog,
    ShopResponse,
    SyncModule,
    SyncTaskResponse,
    UnbindResponse,
)
from app.services.credential_service import CredentialService, refresh_lead, view_from_row
from app.services.shop_health import evaluate_shop_health

_MODULES = {"order", "product", "inventory"}


class ShopService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shops = ShopRepository(session)
        self.credentials = ShopCredentialRepository(session)
        self.tasks = SyncTaskRepository(session)
        self.api_logs = PlatformApiLogRepository(session)
        self.audit = AuditLogRepository(session)
        self.credentials_svc = CredentialService(session)
        register_builtin_adapters()

    def catalog(self) -> list[PlatformSiteCatalog]:
        return [
            PlatformSiteCatalog(code=code, name=PLATFORM_DISPLAY_NAMES[code], sites=list(supported_sites(code)))
            for code in ("amazon", "shopee", "lazada", "tiktok")
        ]

    async def list_shops(self, *, page: int, page_size: int) -> PageData[ShopResponse]:
        rows, total = await self.shops.list_page(page=page, page_size=page_size)
        creds = await self.credentials.map_by_shop_ids([row.id for row in rows])
        items = [self._shop_response(row, creds.get(row.id)) for row in rows]
        return build_page(items, page, page_size, total)

    async def get_shop(self, shop_id: int) -> ShopResponse:
        shop = await self.shops.get_or_404(shop_id)
        cred = await self.credentials.get_by_shop_id(shop.id)
        return self._shop_response(shop, cred)

    async def build_auth_url(
        self,
        *,
        platform: str,
        site_code: str,
        user: SysUser,
        tenant: Tenant,
    ) -> AuthUrlResponse:
        self._ensure_can_bind(user)
        platform_code = platform.lower()
        site = site_code.upper()
        if not adapter_registry.supports(platform_code):
            raise PlatformUnsupportedError(f"平台 {platform} 尚未接入")
        if not site_supported(platform_code, site):
            raise ParamInvalidError(f"{platform_code} 不支持站点 {site}")
        state = create_purpose_token(
            user_id=user.id,
            tenant_id=tenant.id,
            purpose="shop_oauth",
            action=f"{platform_code}:{site}",
            ttl=timedelta(minutes=settings.oauth_state_ttl_minutes),
        )
        redirect = self._redirect_uri(platform_code, state)
        if use_fixture_transport():
            # 没有 Sandbox 凭证时，浏览器直接回到本系统完成换票，而不是跳进真实平台。
            url = redirect
        else:
            url = adapter_registry.get(platform_code).build_auth_url(redirect, state, site_code=site)
        return AuthUrlResponse(url=url, state=state, platform=platform_code, site_code=site)

    async def complete_callback(
        self,
        *,
        platform: str,
        code: str,
        state: str,
        user: SysUser,
        tenant: Tenant,
    ) -> ShopResponse:
        self._ensure_can_bind(user)
        platform_code = platform.lower()
        site = await self._consume_state(state, user_id=user.id, tenant_id=tenant.id, platform=platform_code)
        adapter = adapter_registry.get(platform_code)
        try:
            bundle = await adapter.exchange_token(code, site_code=site)
        except AdapterError as exc:
            raise AppError(str(exc), code=ErrorCode.GRANT_FAILED) from exc
        shop = await self.shops.find_active(
            platform_code=platform_code,
            site_code=site,
            platform_shop_id=bundle.platform_shop_id,
        )
        if shop is None:
            shop = await self.shops.add(
                Shop(
                    tenant_id=tenant.id,
                    platform_code=platform_code,
                    site_code=site,
                    shop_name=bundle.shop_name,
                    platform_shop_id=bundle.platform_shop_id,
                    status=int(ShopStatus.ACTIVE),
                )
            )
            await self.credentials_svc.store_new(shop, bundle)
        else:
            shop.shop_name = bundle.shop_name
            shop.status = int(ShopStatus.ACTIVE)
            shop.last_error = None
            existing = await self.credentials.get_by_shop_id(shop.id)
            if existing is None:
                await self.credentials_svc.store_new(shop, bundle)
            else:
                await self.credentials_svc.replace(existing, bundle)
        await self.audit.append_action(
            tenant_id=tenant.id,
            user_id=user.id,
            action=AuditAction.SHOP_GRANT,
            resource="shop",
            resource_id=shop.id,
            after={"platform": platform_code, "site_code": site, "platform_shop_id": bundle.platform_shop_id},
        )
        cred = await self.credentials.get_by_shop_id(shop.id)
        return self._shop_response(shop, cred)

    async def unbind(self, shop_id: int, *, tenant_id: int, actor_user_id: int) -> UnbindResponse:
        shop = await self.shops.get_or_404(shop_id)
        now = datetime.now(UTC)
        retain_until = now + timedelta(days=settings.shop_data_retain_days)
        shop.status = int(ShopStatus.UNBOUND)
        shop.unbound_at = now
        shop.data_retain_until = retain_until
        await self.shops.soft_delete(shop)
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_user_id,
            action=AuditAction.SHOP_REVOKE,
            resource="shop",
            resource_id=shop.id,
            after={"data_retain_until": retain_until.isoformat(), "platform": shop.platform_code},
        )
        return UnbindResponse(status="unbound", data_retain_until=retain_until)

    async def trigger_sync(
        self,
        shop_id: int,
        *,
        module: SyncModule,
        since: datetime | None,
        until: datetime | None,
        tenant_id: int,
    ) -> SyncTaskResponse:
        if module not in _MODULES:
            raise ParamInvalidError("同步模块只支持 order、product、inventory")
        shop = await self.shops.get_or_404(shop_id)
        if shop.status == int(ShopStatus.AUTH_EXPIRED):
            raise AppError("店铺授权已过期，请重新授权", code=ErrorCode.SHOP_GRANT_EXPIRED)
        if module == "order":
            from app.services.order_sync import OrderSyncService

            synced = await OrderSyncService(self.session).run(
                shop, trigger=SyncTrigger.MANUAL, since=since, until=until
            )
            if synced.payload.get("status") == "skipped" or synced.task_id is None:
                raise AppError("店铺当前不能同步订单", code=ErrorCode.SHOP_GRANT_EXPIRED)
            stored = await self.tasks.get_or_404(synced.task_id)
            return self._task_response(stored)
        now = datetime.now(UTC)
        window_since = since or (now - timedelta(days=1))
        window_until = until or now
        if window_since >= window_until:
            raise ParamInvalidError("同步开始时间必须早于结束时间")
        task = await self.tasks.add(
            SyncTask(
                tenant_id=tenant_id,
                shop_id=shop.id,
                module=module,
                trigger_type=int(SyncTrigger.MANUAL),
                status=int(SyncStatus.RUNNING),
                started_at=now,
                since=window_since,
                until=window_until,
                stats={},
            )
        )
        cred = await self.credentials.get_by_shop_id(shop.id)
        if cred is None:
            return await self._finish_failed(task, shop, "店铺凭证不存在")
        adapter = adapter_registry.get(shop.platform_code)
        view = view_from_row(shop, cred)
        started = time.perf_counter()
        try:
            stats = await self._pull(adapter, view, module, window_since, window_until)
        except AdapterError as exc:
            await self._write_api_log(
                shop,
                endpoint=f"{module}.fetch",
                http_status=exc.http_status,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code=exc.platform_code or exc.decision.value,
            )
            return await self._finish_failed(task, shop, str(exc))
        await self._write_api_log(
            shop,
            endpoint=f"{module}.fetch",
            http_status=200,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error_code=None,
        )
        finished = datetime.now(UTC)
        task.status = int(SyncStatus.SUCCESS)
        task.finished_at = finished
        task.stats = stats
        task.error = None
        shop.last_sync_at = finished
        shop.last_sync_status = int(SyncStatus.SUCCESS)
        shop.last_error = None
        await self.session.flush()
        return self._task_response(task)

    async def list_tasks(
        self,
        *,
        cursor: str | None,
        limit: int,
        shop_id: int | None,
        module: str | None,
        status: int | None,
        created_from: datetime | None,
        created_to: datetime | None,
    ) -> PageData[SyncTaskResponse]:
        decoded = decode_cursor(cursor) if cursor else {}
        raw_id = decoded.get("id")
        before_id = int(raw_id) if raw_id and str(raw_id).isdigit() else None
        rows = await self.tasks.list_cursor(
            limit=limit,
            before_id=before_id,
            shop_id=shop_id,
            module=module,
            status=status,
            created_from=created_from,
            created_to=created_to,
        )
        items = [self._task_response(row) for row in rows]
        return build_cursor_page(items, limit)

    async def get_task(self, task_id: int) -> SyncTaskResponse:
        return self._task_response(await self.tasks.get_or_404(task_id))

    async def _pull(
        self,
        adapter: PlatformAdapter,
        view: CredentialView,
        module: str,
        since: datetime,
        until: datetime,
    ) -> dict[str, object]:
        if module == "order":
            page = await adapter.fetch_orders(view, since=since, until=until)
            orders = page.items
            first = _order_preview(orders[0]) if orders else None
            return {"pulled": len(orders), "first_order": first}
        if module == "product":
            await adapter.fetch_products(view)
        else:
            await adapter.fetch_inventory(view, sku_ids=[])
        return {"pulled": 0}

    async def _finish_failed(self, task: SyncTask, shop: Shop, message: str) -> SyncTaskResponse:
        finished = datetime.now(UTC)
        reason = message[:512]
        task.status = int(SyncStatus.FAILED)
        task.finished_at = finished
        task.error = reason
        shop.last_sync_at = finished
        shop.last_sync_status = int(SyncStatus.FAILED)
        shop.last_error = reason
        await self.session.flush()
        return self._task_response(task)

    async def _write_api_log(
        self,
        shop: Shop,
        *,
        endpoint: str,
        http_status: int | None,
        latency_ms: int,
        error_code: str | None,
    ) -> None:
        await self.api_logs.add(
            PlatformApiLog(
                tenant_id=shop.tenant_id,
                shop_id=shop.id,
                platform_code=shop.platform_code,
                endpoint=endpoint[:255],
                http_status=http_status,
                latency_ms=latency_ms,
                retry_count=0,
                request_id=get_trace_id(),
                error_code=error_code,
            )
        )

    async def _consume_state(self, token: str, *, user_id: int, tenant_id: int, platform: str) -> str:
        import time as time_mod

        from app.core.token_blacklist import get_token_blacklist

        try:
            payload = decode_purpose_token(token, "shop_oauth")
        except AppError as exc:
            raise AppError("授权状态无效或已过期", code=ErrorCode.GRANT_FAILED) from exc
        action = payload.action or ""
        expected_prefix = f"{platform}:"
        if (
            payload.sub != user_id
            or payload.tid != tenant_id
            or not action.startswith(expected_prefix)
            or await get_token_blacklist().is_revoked(payload.jti)
        ):
            raise AppError("授权状态无效或已使用", code=ErrorCode.GRANT_FAILED)
        ttl = max(1, payload.exp - int(time_mod.time()))
        await get_token_blacklist().revoke(payload.jti, ttl)
        return action.split(":", 1)[1]

    def _ensure_can_bind(self, user: SysUser) -> None:
        if user.email_verified_at is None:
            raise AppError("邮箱未验证，不能绑定店铺", code=ErrorCode.EMAIL_NOT_VERIFIED)

    def _redirect_uri(self, platform: str, state: str) -> str:
        if use_fixture_transport():
            base = settings.frontend_base_url.rstrip("/")
            return f"{base}/shops?platform={platform}&code=fixture&state={state}"
        return f"{settings.oauth_redirect_base_url.rstrip('/')}/{platform}"

    def _shop_response(self, shop: Shop, cred: ShopCredential | None) -> ShopResponse:
        now = datetime.now(UTC)
        health, reason = evaluate_shop_health(
            status=shop.status,
            refresh_fail_count=cred.refresh_fail_count if cred else 0,
            expires_at=cred.expires_at if cred else None,
            last_sync_at=shop.last_sync_at,
            last_sync_status=shop.last_sync_status,
            last_error=shop.last_error,
            now=now,
            lead=refresh_lead(),
            stale=timedelta(minutes=settings.shop_sync_stale_minutes),
            alert_threshold=settings.token_refresh_alert_threshold,
        )
        return ShopResponse(
            id=shop.id,
            platform_code=shop.platform_code,
            site_code=shop.site_code,
            shop_name=shop.shop_name,
            platform_shop_id=shop.platform_shop_id,
            status=shop.status,
            health=health,
            health_reason=reason,
            last_sync_at=shop.last_sync_at,
            last_sync_status=shop.last_sync_status,
            last_error=shop.last_error,
            auth_expires_at=cred.expires_at if cred else None,
            data_retain_until=shop.data_retain_until,
        )

    def _task_response(self, task: SyncTask) -> SyncTaskResponse:
        return SyncTaskResponse(
            id=task.id,
            shop_id=task.shop_id,
            module=task.module,
            trigger_type=task.trigger_type,
            status=task.status,
            started_at=task.started_at,
            finished_at=task.finished_at,
            since=task.since,
            until=task.until,
            stats=task.stats or {},
            error=task.error,
            created_at=task.created_at,
        )


def _order_preview(order: UnifiedOrder) -> dict[str, object]:
    amount = order.total_amount if isinstance(order.total_amount, Decimal) else Decimal(str(order.total_amount))
    return {
        "platform_order_id": order.platform_order_id,
        "unified_status": order.unified_status,
        "currency": order.currency,
        "total_amount": money_to_str(amount),
    }


__all__ = ["ShopService"]
