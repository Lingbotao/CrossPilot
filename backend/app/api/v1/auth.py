"""认证接口（PRD 10.3 批次 1）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentIdentity
from app.core.request_info import client_ip, client_ua
from app.core.response import ApiResponse, ok
from app.db.session import get_db
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    MeResponse,
    RefreshRequest,
    RefreshResponse,
)
from app.services.auth_service import AuthService, build_current_user

router = APIRouter(prefix="/auth", tags=["认证"])

DbDep = Depends(get_db)


@router.post("/login", response_model=ApiResponse[LoginResponse], summary="登录")
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = DbDep,
) -> ApiResponse[LoginResponse]:
    """登录并签发双 Token。

    - 用户属于多个租户时必须传 ``tenant_code``，否则返回统一失败话术；
    - 连续失败达阈值会锁定账号（``LOGIN_MAX_FAILURES`` / ``LOGIN_LOCK_MINUTES``）；
    - 失败原因不对外区分，避免接口被当成账号枚举器。
    """
    service = AuthService(session)
    result = await service.login(payload, ip=client_ip(request), ua=client_ua(request))
    return ok(result, message="登录成功")


@router.post("/refresh", response_model=ApiResponse[RefreshResponse], summary="刷新令牌")
async def refresh_token(
    payload: RefreshRequest,
    session: AsyncSession = DbDep,
) -> ApiResponse[RefreshResponse]:
    """用 Refresh Token 换一对新令牌（Refresh 会轮换，旧的即失效）。"""
    service = AuthService(session)
    return ok(await service.refresh(payload.refresh_token))


@router.post("/logout", response_model=ApiResponse[dict[str, str]], summary="登出")
async def logout(
    request: Request,
    identity: CurrentIdentity,
    session: AsyncSession = DbDep,
) -> ApiResponse[dict[str, str]]:
    """登出。

    说明：Access Token 是自包含签名的，服务端不做状态存储 ——
    真正的失效靠"Access 15 分钟自然过期 + Refresh 不再可用"。
    这里记录审计，并由前端清理本地令牌。
    """
    await AuthService(session).logout(
        identity.user.id,
        int(identity.tenant.id),
        client_ip(request),
        client_ua(request),
    )
    return ok({"status": "logged_out"}, message="已登出")


@router.get("/me", response_model=ApiResponse[MeResponse], summary="当前用户与租户上下文")
async def me(identity: CurrentIdentity, session: AsyncSession = DbDep) -> ApiResponse[MeResponse]:
    """前端刷新页面后的上下文恢复入口。

    返回：用户信息 + 租户信息 + 角色 + **权限点列表** + 数据范围。
    前端 ``PermissionGuard`` 与 ``usePermission`` 都基于这里的 ``permissions``。
    """
    service = AuthService(session)
    tenant_context = await service.build_tenant_context(identity.user, identity.tenant, identity.membership)
    return ok(MeResponse(user=build_current_user(identity.user), tenant=tenant_context))
