"""认证接口（PRD 10.3 批次 1）。"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentIdentity
from app.core.request_info import client_ip, client_ua
from app.core.response import ApiResponse, ok
from app.db.session import get_db
from app.schemas.auth import (
    ConfirmPasswordRequest,
    ConfirmPasswordResponse,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    VerifyEmailRequest,
)
from app.services.auth_service import AuthService, build_current_user
from app.services.registration_service import RegistrationService

router = APIRouter(prefix="/auth", tags=["认证"])

DbDep = Depends(get_db)


@router.post(
    "/register",
    response_model=ApiResponse[RegisterResponse],
    status_code=201,
    summary="注册租户",
)
async def register(payload: RegisterRequest, session: AsyncSession = DbDep) -> ApiResponse[RegisterResponse]:
    result = await RegistrationService(session).register(payload)
    return ok(result, message="注册成功，请完成邮箱验证")


@router.post(
    "/verify-email",
    response_model=ApiResponse[dict[str, str]],
    summary="验证邮箱",
)
async def verify_email(payload: VerifyEmailRequest, session: AsyncSession = DbDep) -> ApiResponse[dict[str, str]]:
    await RegistrationService(session).verify_email(payload.token)
    return ok({"status": "verified"}, message="邮箱验证成功")


@router.post(
    "/resend-verification",
    response_model=ApiResponse[dict[str, str]],
    summary="重发验证邮件",
)
async def resend_verification(
    payload: ResendVerificationRequest, session: AsyncSession = DbDep
) -> ApiResponse[dict[str, str]]:
    await RegistrationService(session).resend_verification(str(payload.email))
    return ok({"status": "accepted"}, message="如果账号存在，验证链接已重新投递")


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
    payload: LogoutRequest = Body(default_factory=LogoutRequest),
    session: AsyncSession = DbDep,
) -> ApiResponse[dict[str, str]]:
    """登出：把当前 Access / Refresh 的 jti 写入黑名单（A-02），并记审计。

    前端必须把 Refresh Token 一并送来，否则 7 天续期令牌仍能换新 Access。
    """
    await AuthService(session).logout(
        user_id=identity.user.id,
        tenant_id=int(identity.tenant.id),
        access_payload=identity.payload,
        refresh_token=payload.refresh_token,
        ip=client_ip(request),
        ua=client_ua(request),
    )
    return ok({"status": "logged_out"}, message="已登出")


@router.post(
    "/confirm-password",
    response_model=ApiResponse[ConfirmPasswordResponse],
    summary="敏感操作二次确认",
)
async def confirm_password(
    payload: ConfirmPasswordRequest,
    identity: CurrentIdentity,
    session: AsyncSession = DbDep,
) -> ApiResponse[ConfirmPasswordResponse]:
    result = AuthService(session).confirm_password(identity.user, payload.password, payload.action, identity.tenant.id)
    return ok(result, message="确认成功")


@router.get("/me", response_model=ApiResponse[MeResponse], summary="当前用户与租户上下文")
async def me(identity: CurrentIdentity, session: AsyncSession = DbDep) -> ApiResponse[MeResponse]:
    """前端刷新页面后的上下文恢复入口。

    返回：用户信息 + 租户信息 + 角色 + **权限点列表** + 数据范围。
    前端 ``PermissionGuard`` 与 ``usePermission`` 都基于这里的 ``permissions``。
    """
    service = AuthService(session)
    tenant_context = await service.build_tenant_context(identity.user, identity.tenant, identity.membership)
    return ok(MeResponse(user=build_current_user(identity.user), tenant=tenant_context))
