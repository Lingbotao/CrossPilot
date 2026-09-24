"""认证与租户上下文服务。

**登录是这个系统里唯一必须在"没有租户上下文"的状态下执行的业务逻辑**，
因此顺序极其关键，任何调整都必须满足下面的三步：

    ① 用 SECURITY DEFINER 函数解析该用户的租户成员关系（此刻无租户上下文）
    ② 确定租户 → 写 contextvar → 立刻绑定 RLS 会话变量
    ③ 之后才能查询任何租户表

顺序错了不会"少查点数据"，而是**一行都查不出来**（RLS 默认拒绝）——
这是刻意设计的 fail-closed 姿态。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NoReturn

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import set_role_code, set_tenant_id, set_user_id
from app.core.errors import AccountLockedError, TenantDisabledError, UnauthenticatedError
from app.core.logging import get_logger
from app.core.permissions import (
    ROLE_NAMES_ZH,
    RoleCode,
    permission_codes_for_role,
    permissions_for_role,
    role_can_view_cost,
)
from app.core.security import (
    TokenPayload,
    create_purpose_token,
    create_token_pair,
    decode_token,
    verify_password,
)
from app.core.token_blacklist import assert_token_not_revoked, revoke_payload
from app.db.session import apply_rls_tenant
from app.models.audit import LoginLog
from app.models.enums import AuditAction, LoginResult, TenantStatus, TenantUserStatus, UserStatus
from app.models.tenant import SysUser, Tenant, TenantUser
from app.repositories.identity import (
    AuditLogRepository,
    LoginLogRepository,
    RoleRepository,
    SysUserRepository,
    TenantMembership,
    TenantRepository,
    TenantUserRepository,
    UserDataScopeRepository,
)
from app.schemas.auth import (
    ConfirmPasswordResponse,
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
    RefreshResponse,
    TenantBrief,
    TenantCurrentResponse,
)

log = get_logger(__name__)

# 登录失败的对外统一话术：不区分「邮箱不存在 / 密码错 / 账号停用」，
# 否则接口会变成账号枚举器（PRD 13.2 安全要求）。
_GENERIC_AUTH_FAILURE = "邮箱或密码错误"


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tenants = TenantRepository(session)
        self.users = SysUserRepository(session)
        self.members = TenantUserRepository(session)
        self.roles = RoleRepository(session)
        self.scopes = UserDataScopeRepository(session)
        self.login_logs = LoginLogRepository(session)
        self.audit = AuditLogRepository(session)

    # ------------------------------------------------------------------ 登录
    async def login(self, payload: LoginRequest, *, ip: str | None = None, ua: str | None = None) -> LoginResponse:
        email = payload.email.lower().strip()
        user = await self.users.get_by_email(email)

        # ---- ① 用户校验（不区分失败原因） ----
        if user is None:
            await self._reject_login(UnauthenticatedError(_GENERIC_AUTH_FAILURE), None, email, ip, ua, "user_not_found")

        if SysUserRepository.is_locked(user):
            await self._reject_login(AccountLockedError(), None, email, ip, ua, "account_locked", user_id=user.id)

        if int(user.status) != int(UserStatus.ACTIVE):
            await self._reject_login(
                UnauthenticatedError(_GENERIC_AUTH_FAILURE), None, email, ip, ua, "user_disabled", user_id=user.id
            )

        if not verify_password(payload.password, user.password_hash):
            await self.users.record_login_failure(user)
            await self._reject_login(
                UnauthenticatedError(_GENERIC_AUTH_FAILURE), None, email, ip, ua, "bad_password", user_id=user.id
            )

        # ---- ② 解析租户成员关系（SECURITY DEFINER，无租户上下文） ----
        memberships = await self.members.list_user_tenants_cross_tenant(user.id)
        active = [m for m in memberships if int(m.status) == int(TenantUserStatus.ACTIVE)]
        if not active:
            await self._reject_login(
                UnauthenticatedError("该账号未加入任何可用租户"),
                None,
                email,
                ip,
                ua,
                "no_active_tenant",
                user_id=user.id,
            )

        chosen = self._pick_tenant(active, payload.tenant_code)

        # ---- ③ 建立上下文 + 绑定 RLS（必须在任何租户表查询之前） ----
        set_tenant_id(chosen.tenant_id)
        set_user_id(user.id)
        set_role_code(chosen.role_code)
        await apply_rls_tenant(self.session, chosen.tenant_id)

        tenant = await self._require_active_tenant(chosen.tenant_id)
        membership = await self._require_membership(user.id, chosen.tenant_id)

        # ---- 记账 ----
        await self.users.record_login_success(user)
        await self._log_login(chosen.tenant_id, email, LoginResult.SUCCESS, ip, ua, None, user_id=user.id)
        await self.audit.append_action(
            tenant_id=chosen.tenant_id,
            action=AuditAction.LOGIN,
            resource="auth",
            user_id=user.id,
            resource_id=user.id,
            ip=ip,
            ua=ua,
        )

        pair = create_token_pair(user.id, tenant.id, membership.role_code)
        log.info("login_success", user_id=user.id, tenant_id=tenant.id, role=membership.role_code)

        return LoginResponse(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            expires_in=pair.expires_in,
            tenant=self._to_brief(tenant, membership.role_code),
            available_tenants=[self._to_brief_from_membership(m) for m in active],
        )

    # ------------------------------------------------------------------ 刷新
    async def refresh(self, refresh_token: str) -> RefreshResponse:
        payload: TokenPayload = decode_token(refresh_token, expected_type="refresh")
        await assert_token_not_revoked(payload)
        if payload.tid is None:
            raise UnauthenticatedError("令牌缺少租户信息")

        set_tenant_id(payload.tid)
        set_user_id(payload.sub)
        set_role_code(payload.role)
        await apply_rls_tenant(self.session, payload.tid)

        user = await self.users.get(payload.sub)
        if user is None or int(user.status) != int(UserStatus.ACTIVE):
            raise UnauthenticatedError("账号不可用")
        if SysUserRepository.is_locked(user):
            raise AccountLockedError()

        # Refresh 时重新校验成员关系 —— 被移除的成员不能靠旧 refresh token 续命
        membership = await self._require_membership(user.id, payload.tid)
        await self._require_active_tenant(payload.tid)

        # 密码修改后，旧 refresh token 立即失效
        if user.password_changed_at is not None:
            issued_at = datetime.fromtimestamp(payload.iat, tz=UTC)
            if issued_at < user.password_changed_at:
                raise UnauthenticatedError("密码已变更，请重新登录")

        # 轮换：先作废旧 Refresh，再签发新对。前端续期已单飞，不会并发互踩。
        await revoke_payload(payload)
        pair = create_token_pair(user.id, payload.tid, membership.role_code)
        return RefreshResponse(
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            expires_in=pair.expires_in,
        )

    # ------------------------------------------------------------------ 上下文
    async def build_tenant_context(
        self, user: SysUser, tenant: Tenant, membership: TenantUser
    ) -> TenantCurrentResponse:
        permissions = permissions_for_role(membership.role_code)
        scopes = await self.scopes.list_with_shop_scope(membership.id)
        return TenantCurrentResponse(
            id=tenant.id,
            code=tenant.code,
            name=tenant.name,
            plan=int(tenant.plan),
            status=int(tenant.status),
            default_currency=tenant.default_currency,
            timezone=tenant.timezone,
            role_code=RoleCode(membership.role_code),
            role_name=ROLE_NAMES_ZH.get(RoleCode(membership.role_code), membership.role_code),
            permissions=sorted(p.value for p in permissions),
            can_view_cost=role_can_view_cost(membership.role_code),
            data_scope=scopes,
        )

    async def logout(
        self,
        *,
        user_id: int,
        tenant_id: int,
        access_payload: TokenPayload,
        refresh_token: str | None,
        ip: str | None,
        ua: str | None,
    ) -> None:
        """登出：把当前 Access / Refresh 的 jti 写入黑名单（A-02），并记审计。"""
        await revoke_payload(access_payload)
        if refresh_token:
            try:
                refresh_payload = decode_token(refresh_token, expected_type="refresh")
                await assert_token_not_revoked(refresh_payload)
                await revoke_payload(refresh_payload)
            except UnauthenticatedError:
                # Refresh 已失效或不匹配 —— 仍要让 Access 失效，不把登出变成 401
                log.info("logout_refresh_already_invalid", user_id=user_id)

        await self.audit.append_action(
            tenant_id=tenant_id,
            action=AuditAction.LOGOUT,
            resource="auth",
            user_id=user_id,
            resource_id=user_id,
            ip=ip,
            ua=ua,
        )

    def confirm_password(self, user: SysUser, password: str, action: str, tenant_id: int) -> ConfirmPasswordResponse:
        if not verify_password(password, user.password_hash):
            raise UnauthenticatedError("密码错误")
        token = create_purpose_token(
            user_id=user.id,
            tenant_id=tenant_id,
            purpose="confirmation",
            action=action,
            ttl=timedelta(minutes=settings.confirmation_ttl_minutes),
        )
        return ConfirmPasswordResponse(
            confirmation_token=token,
            expires_in=settings.confirmation_ttl_minutes * 60,
        )

    # ------------------------------------------------------------------ 内部
    async def _reject_login(
        self,
        error: UnauthenticatedError | AccountLockedError,
        tenant_id: int | None,
        email: str,
        ip: str | None,
        ua: str | None,
        fail_reason: str,
        user_id: int | None = None,
    ) -> NoReturn:
        """失败登录必须先落库再抛错。

        ``get_db`` 在异常时会 rollback。若不先 ``commit``，锁定计数和
        ``login_log`` 都会被 401 一起卷走，A-01 的锁定形同虚设。
        """
        await self._log_login(tenant_id, email, LoginResult.FAILURE, ip, ua, fail_reason, user_id=user_id)
        await self.session.commit()
        raise error

    @staticmethod
    def _pick_tenant(memberships: list[TenantMembership], tenant_code: str | None) -> TenantMembership:
        if tenant_code:
            for m in memberships:
                if m.code == tenant_code:
                    return m
            # 不区分「租户不存在」与「你不是该租户成员」
            raise UnauthenticatedError("该账号未加入指定租户")
        if len(memberships) > 1:
            raise UnauthenticatedError("该账号属于多个租户，请指定 tenant_code")
        return memberships[0]

    async def _require_active_tenant(self, tenant_id: int) -> Tenant:
        tenant = await self.tenants.get(tenant_id)
        if tenant is None:
            raise TenantDisabledError("租户不存在")
        if int(tenant.status) == int(TenantStatus.DISABLED):
            raise TenantDisabledError()
        return tenant

    async def _require_membership(self, user_id: int, tenant_id: int) -> TenantUser:
        membership = await self.members.get_membership(user_id)
        if membership is None or int(membership.status) != int(TenantUserStatus.ACTIVE):
            raise UnauthenticatedError("成员关系已失效，请联系管理员")
        if int(membership.tenant_id) != int(tenant_id):
            raise UnauthenticatedError("成员关系与租户不匹配")
        return membership

    async def _log_login(
        self,
        tenant_id: int | None,
        email: str,
        result: LoginResult,
        ip: str | None,
        ua: str | None,
        fail_reason: str | None,
        user_id: int | None = None,
    ) -> None:
        row = LoginLog.build(
            tenant_id=tenant_id,
            result=result,
            user_id=user_id,
            email=email,
            ip=ip,
            ua=ua,
            fail_reason=fail_reason,
        )
        self.session.add(row)
        await self.session.flush()

    @staticmethod
    def _to_brief(tenant: Tenant, role_code: str) -> TenantBrief:
        return TenantBrief(
            id=tenant.id,
            code=tenant.code,
            name=tenant.name,
            role_code=RoleCode(role_code),
        )

    @staticmethod
    def _to_brief_from_membership(m: TenantMembership) -> TenantBrief:
        return TenantBrief(id=m.tenant_id, code=m.code, name=m.name, role_code=RoleCode(m.role_code))


def build_current_user(user: SysUser) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        last_login_at=user.last_login_at,
        email_verified_at=user.email_verified_at,
    )


__all__ = ["AuthService", "build_current_user", "permission_codes_for_role"]
