"""租户注册与邮箱验证。"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import set_role_code, set_tenant_id, set_user_id
from app.core.errors import AppError, ErrorCode, ParamInvalidError, TokenExpiredError, UnauthenticatedError
from app.core.permissions import (
    ROLE_DESCRIPTIONS_ZH,
    ROLE_NAMES_ZH,
    RoleCode,
    permission_codes_for_role,
)
from app.core.security import (
    create_purpose_token,
    decode_purpose_token,
    hash_password,
    password_strength_error,
)
from app.db.session import apply_rls_tenant
from app.db.snowflake import next_snowflake_id
from app.models.enums import AuditAction, DataScopeType, ResourceType
from app.models.tenant import Role, UserDataScope
from app.repositories.identity import (
    AuditLogRepository,
    RoleRepository,
    SysUserRepository,
    TenantMembership,
    TenantRepository,
    UserDataScopeRepository,
)
from app.schemas.auth import RegisterRequest, RegisterResponse, TenantBrief
from app.services.outbox import deliver_link


class RegistrationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tenants = TenantRepository(session)
        self.users = SysUserRepository(session)
        self.roles = RoleRepository(session)
        self.scopes = UserDataScopeRepository(session)
        self.audit = AuditLogRepository(session)

    async def register(self, payload: RegisterRequest) -> RegisterResponse:
        strength_error = password_strength_error(payload.password)
        if strength_error:
            raise ParamInvalidError(strength_error)

        tenant_id = next_snowflake_id()
        user_id = next_snowflake_id()
        membership_id = next_snowflake_id()
        email = payload.email.lower().strip()
        try:
            registered = await self.tenants.register(
                tenant_id=tenant_id,
                user_id=user_id,
                membership_id=membership_id,
                email=email,
                password_hash=hash_password(payload.password),
                display_name=payload.display_name,
                tenant_code=payload.tenant_code,
                tenant_name=payload.tenant_name,
                default_currency=payload.default_currency,
                timezone=payload.timezone,
            )
        except IntegrityError as exc:
            await self.session.rollback()
            detail = str(exc.orig).lower()
            if "email" in detail:
                raise AppError("该邮箱已注册", code=ErrorCode.EMAIL_ALREADY_REGISTERED) from exc
            if "tenant" in detail or "code" in detail:
                raise AppError("租户标识已被使用", code=ErrorCode.TENANT_CODE_TAKEN) from exc
            raise

        set_tenant_id(registered.tenant_id)
        set_user_id(registered.user_id)
        set_role_code(RoleCode.OWNER.value)
        await apply_rls_tenant(self.session, registered.tenant_id)
        await self._bootstrap_tenant(registered.membership_id)

        await self.audit.append_action(
            tenant_id=registered.tenant_id,
            action=AuditAction.TENANT_REGISTER,
            resource="tenant",
            user_id=registered.user_id,
            resource_id=registered.tenant_id,
            after={"code": payload.tenant_code, "name": payload.tenant_name},
        )
        # outbox 是事务外副作用；先提交，避免数据库回滚后仍出现可用验证链接。
        await self.session.commit()
        self._deliver_verification(
            user_id=registered.user_id,
            tenant_id=registered.tenant_id,
            email=email,
        )
        return RegisterResponse(
            tenant=TenantBrief(
                id=registered.tenant_id,
                code=payload.tenant_code,
                name=payload.tenant_name,
                role_code=RoleCode.OWNER,
            )
        )

    async def verify_email(self, token: str) -> None:
        try:
            payload = decode_purpose_token(token, "email_verify")
        except TokenExpiredError as exc:
            raise AppError("验证链接已过期", code=ErrorCode.VERIFICATION_EXPIRED) from exc
        except UnauthenticatedError as exc:
            raise ParamInvalidError("验证链接无效") from exc

        if payload.tid is None or not payload.email:
            raise ParamInvalidError("验证链接无效")
        set_tenant_id(payload.tid)
        set_user_id(payload.sub)
        await apply_rls_tenant(self.session, payload.tid)
        user = await self.users.get(payload.sub)
        if user is None or user.email.lower() != payload.email.lower():
            raise ParamInvalidError("验证链接无效")
        await self.users.mark_email_verified(user)

    async def resend_verification(self, email: str) -> None:
        """无论邮箱是否存在都返回成功，避免账号枚举。"""
        user = await self.users.get_by_email(email)
        if user is None or user.email_verified_at is not None:
            return
        memberships = await self._memberships(user.id)
        if not memberships:
            return
        self._deliver_verification(
            user_id=user.id,
            tenant_id=memberships[0].tenant_id,
            email=user.email,
        )

    async def _bootstrap_tenant(self, membership_id: int) -> None:
        for role_code in RoleCode:
            await self.roles.add(
                Role(
                    code=role_code.value,
                    name=ROLE_NAMES_ZH[role_code],
                    is_system=True,
                    permission_codes=permission_codes_for_role(role_code.value),
                    description=ROLE_DESCRIPTIONS_ZH[role_code],
                )
            )
        await self.scopes.add(
            UserDataScope(
                tenant_user_id=membership_id,
                resource_type=int(ResourceType.SHOP),
                scope_type=int(DataScopeType.ALL),
                shop_ids=[],
            )
        )

    async def _memberships(self, user_id: int) -> list[TenantMembership]:
        from app.repositories.identity import TenantUserRepository

        return await TenantUserRepository(self.session).list_user_tenants_cross_tenant(user_id)

    @staticmethod
    def _deliver_verification(*, user_id: int, tenant_id: int, email: str) -> None:
        token = create_purpose_token(
            user_id=user_id,
            tenant_id=tenant_id,
            purpose="email_verify",
            email=email,
            ttl=timedelta(hours=settings.email_verification_ttl_hours),
        )
        deliver_link(kind="email_verification", recipient=email, path="/verify-email", token=token)


__all__ = ["RegistrationService"]
