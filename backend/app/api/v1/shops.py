"""店铺授权接口。"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.core.deps import DbSession, Identity, require_confirmation, require_permission
from app.core.errors import PermissionDeniedError
from app.core.pagination import PageData
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.shop import (
    AuthUrlRequest,
    AuthUrlResponse,
    OAuthCallbackRequest,
    PlatformSiteCatalog,
    ShopResponse,
    SyncRequest,
    SyncTaskResponse,
    UnbindResponse,
)
from app.services.shop_service import ShopService

router = APIRouter(prefix="/shops", tags=["店铺授权"])

ShopReader = Annotated[Identity, Depends(require_permission(Perm.SHOP_READ))]
ShopGranter = Annotated[Identity, Depends(require_permission(Perm.SHOP_GRANT))]


@router.get("/catalog", response_model=ApiResponse[list[PlatformSiteCatalog]], summary="可授权平台与站点")
async def catalog(identity: ShopReader, session: DbSession) -> ApiResponse[list[PlatformSiteCatalog]]:
    del identity
    return ok(ShopService(session).catalog())


@router.get("", response_model=ApiResponse[PageData[ShopResponse]], summary="店铺列表")
async def list_shops(
    identity: ShopReader,
    session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> ApiResponse[PageData[ShopResponse]]:
    del identity
    return ok(await ShopService(session).list_shops(page=page, page_size=page_size))


@router.post(
    "/{platform}/auth-url",
    response_model=ApiResponse[AuthUrlResponse],
    summary="获取平台授权链接",
)
async def build_auth_url(
    platform: str,
    payload: AuthUrlRequest,
    identity: ShopGranter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[AuthUrlResponse]:
    result = await ShopService(session).build_auth_url(
        platform=platform,
        site_code=payload.site_code,
        user=identity.user,
        tenant=identity.tenant,
    )
    return ok(result)


@router.post(
    "/callback/{platform}",
    response_model=ApiResponse[ShopResponse],
    summary="完成授权回调",
)
async def oauth_callback(
    platform: str,
    payload: OAuthCallbackRequest,
    identity: ShopGranter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ShopResponse]:
    shop = await ShopService(session).complete_callback(
        platform=platform,
        code=payload.code,
        state=payload.state,
        user=identity.user,
        tenant=identity.tenant,
    )
    return ok(shop, message="店铺已授权")


@router.get("/oauth/callback/{platform}", summary="浏览器授权回跳", include_in_schema=True)
async def oauth_browser_callback(platform: str, code: str, state: str) -> RedirectResponse:
    target = (
        f"{settings.frontend_base_url.rstrip('/')}/shops"
        f"?platform={quote(platform)}&code={quote(code)}&state={quote(state)}"
    )
    return RedirectResponse(target, status_code=302)


@router.get("/{shop_id}", response_model=ApiResponse[ShopResponse], summary="店铺详情")
async def get_shop(shop_id: int, identity: ShopReader, session: DbSession) -> ApiResponse[ShopResponse]:
    del identity
    return ok(await ShopService(session).get_shop(shop_id))


@router.post(
    "/{shop_id}/sync",
    response_model=ApiResponse[SyncTaskResponse],
    summary="手动触发同步",
)
async def trigger_sync(
    shop_id: int,
    payload: SyncRequest,
    identity: ShopGranter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[SyncTaskResponse]:
    task = await ShopService(session).trigger_sync(
        shop_id,
        module=payload.module,
        since=payload.since,
        until=payload.until,
        tenant_id=identity.tenant.id,
    )
    return ok(task, message="同步已执行")


@router.delete(
    "/{shop_id}",
    response_model=ApiResponse[UnbindResponse],
    summary="解绑店铺",
)
async def unbind_shop(
    shop_id: int,
    identity: Annotated[Identity, Depends(require_confirmation("shop.unbind"))],
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[UnbindResponse]:
    if Perm.SHOP_GRANT not in identity.permissions:
        raise PermissionDeniedError("缺少权限：shop:grant")
    result = await ShopService(session).unbind(shop_id, tenant_id=identity.tenant.id, actor_user_id=identity.user.id)
    return ok(result, message="店铺已解绑，历史数据按保留期保存")
