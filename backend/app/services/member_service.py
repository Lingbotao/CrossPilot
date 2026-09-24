"""成员邀请、角色与数据范围业务。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import set_role_code, set_tenant_id, set_user_id
from app.core.errors import (
    AppError,
    ConflictError,
    ErrorCode,
    NotFoundError,
    ParamInvalidError,
    TokenExpiredError,
    UnauthenticatedError,
)
from app.core.pagination import PageInfo
from app.core.permissions import RoleCode
from app.core.security import (
    create_purpose_token,
    decode_purpose_token,
    hash_password,
    password_strength_error,
    verify_password,
)
from app.db.session import apply_rls_tenant
from app.models.enums import (
    AuditAction,
    DataScopeType,
    InvitationStatus,
    ResourceType,
    TenantUserStatus,
    UserStatus,
)
from app.models.tenant import MemberInvitation, SysUser, TenantUser
from app.repositories.identity import (
    AuditLogRepository,
    MemberInvitationRepository,
    RoleRepository,
    SysUserRepository,
    TenantUserRepository,
    UserDataScopeRepository,
)
from app.schemas.member import (
    AcceptedInvitationResponse,
    InvitationResponse,
    InviteMemberRequest,
    MemberListResponse,
    MemberResponse,
    RoleResponse,
    UpdateDataScopeRequest,
)
from app.services.outbox import deliver_link


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class MemberService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.members = TenantUserRepository(session)
        self.users = SysUserRepository(session)
        self.roles = RoleRepository(session)
        self.scopes = UserDataScopeRepository(session)
        self.invitations = MemberInvitationRepository(session)
        self.audit = AuditLogRepository(session)

    async def list_members(self, *, page: int, page_size: int) -> MemberListResponse:
        records, total = await self.members.list_members(offset=(page - 1) * page_size, limit=page_size)
        items: list[MemberResponse] = []
        for record in records:
            membership = record.membership
            items.append(
                MemberResponse(
                    id=membership.id,
                    user_id=record.user.id,
                    email=record.user.email,
                    display_name=record.user.display_name,
                    role_code=RoleCode(membership.role_code),
                    status=int(membership.status),
                    joined_at=membership.joined_at,
                    data_scope=await self.scopes.list_with_shop_scope(membership.id),
                )
            )
        invitations = [self._invitation_response(row) for row in await self.invitations.list_pending()]
        return MemberListResponse(
            members=items,
            invitations=invitations,
            page_info=PageInfo(
                total=total,
                page=page,
                page_size=page_size,
                has_more=page * page_size < total,
            ),
        )

    async def list_roles(self) -> list[RoleResponse]:
        roles = await self.roles.list_for_tenant()
        return [
            RoleResponse(
                code=RoleCode(role.code),
                name=role.name,
                is_system=role.is_system,
                permission_codes=list(role.permission_codes),
                description=role.description,
            )
            for role in roles
            if role.is_system
        ]

    async def invite(self, payload: InviteMemberRequest, *, tenant_id: int, actor_user_id: int) -> InvitationResponse:
        role = await self.roles.get_by_code(payload.role_code.value)
        if role is None or not role.is_system:
            raise ParamInvalidError("角色不存在")
        email = payload.email.lower().strip()
        await self.invitations.revoke_pending_for_email(email)
        invitation_id = self._new_id()
        token = create_purpose_token(
            user_id=invitation_id,
            tenant_id=tenant_id,
            purpose="member_invite",
            email=email,
            ttl=timedelta(hours=settings.member_invitation_ttl_hours),
        )
        invitation = await self.invitations.add(
            MemberInvitation(
                id=invitation_id,
                email=email,
                role_code=payload.role_code.value,
                token_hash=_token_hash(token),
                expires_at=datetime.now(UTC) + timedelta(hours=settings.member_invitation_ttl_hours),
                status=int(InvitationStatus.PENDING),
                invited_by=actor_user_id,
            )
        )
        await self.audit.append_action(
            tenant_id=tenant_id,
            action=AuditAction.MEMBER_INVITE,
            resource="member",
            user_id=actor_user_id,
            resource_id=invitation.id,
            after={"email": email, "role_code": payload.role_code.value},
        )
        # outbox 是事务外副作用；先持久化邀请与审计，再暴露邀请链接。
        await self.session.commit()
        deliver_link(
            kind="member_invitation",
            recipient=email,
            path="/invite/accept",
            token=token,
        )
        return self._invitation_response(invitation)

    async def revoke_invitation(self, invitation_id: int, *, tenant_id: int, actor_user_id: int) -> None:
        invitation = await self.invitations.get_or_404(invitation_id)
        if int(invitation.status) != int(InvitationStatus.PENDING):
            raise ConflictError("邀请已失效")
        await self.invitations.set_status(invitation, InvitationStatus.REVOKED)
        await self.audit.append_action(
            tenant_id=tenant_id,
            action=AuditAction.MEMBER_REMOVE,
            resource="member_invitation",
            user_id=actor_user_id,
            resource_id=invitation.id,
            before={"email": invitation.email, "status": int(InvitationStatus.PENDING)},
            after={"status": int(InvitationStatus.REVOKED)},
        )

    async def accept(self, token: str, password: str, display_name: str | None) -> AcceptedInvitationResponse:
        try:
            payload = decode_purpose_token(token, "member_invite")
        except TokenExpiredError as exc:
            raise AppError("邀请链接已过期", code=ErrorCode.INVITATION_EXPIRED) from exc
        except UnauthenticatedError as exc:
            raise AppError("邀请链接无效", code=ErrorCode.INVITATION_INVALID) from exc
        if payload.tid is None or not payload.email:
            raise AppError("邀请链接无效", code=ErrorCode.INVITATION_INVALID)

        set_tenant_id(payload.tid)
        await apply_rls_tenant(self.session, payload.tid)
        invitation = await self.invitations.get_pending_by_hash(_token_hash(token))
        if invitation is None or invitation.id != payload.sub:
            raise AppError("邀请链接无效或已使用", code=ErrorCode.INVITATION_INVALID)

        user = await self.users.get_by_email(payload.email)
        if user is None:
            strength_error = password_strength_error(password)
            if strength_error:
                raise ParamInvalidError(strength_error)
            user = await self.users.add(
                SysUser(
                    email=payload.email,
                    password_hash=hash_password(password),
                    display_name=display_name,
                    status=int(UserStatus.ACTIVE),
                    email_verified_at=datetime.now(UTC),
                )
            )
        elif not verify_password(password, user.password_hash):
            raise AppError("账号密码错误", code=ErrorCode.INVITATION_INVALID)
        elif user.email_verified_at is None:
            await self.users.mark_email_verified(user)

        if await self.members.get_by_user_id(user.id):
            raise ConflictError("该用户已是当前租户成员")
        membership = await self.members.add(
            TenantUser(
                user_id=user.id,
                role_code=invitation.role_code,
                status=int(TenantUserStatus.ACTIVE),
                joined_at=datetime.now(UTC),
            )
        )
        await self.scopes.upsert(
            tenant_user_id=membership.id,
            resource_type=int(ResourceType.SHOP),
            scope_type=int(DataScopeType.ALL),
            shop_ids=[],
        )
        await self.invitations.set_status(invitation, InvitationStatus.ACCEPTED)
        set_user_id(user.id)
        set_role_code(membership.role_code)
        await self.audit.append_action(
            tenant_id=payload.tid,
            action=AuditAction.MEMBER_ACCEPT,
            resource="member",
            user_id=user.id,
            resource_id=membership.id,
            after={"role_code": membership.role_code},
        )
        return AcceptedInvitationResponse(tenant_id=payload.tid, member_id=membership.id)

    async def change_role(
        self,
        member_id: int,
        role_code: RoleCode,
        *,
        tenant_id: int,
        actor_user_id: int,
    ) -> MemberResponse:
        member = await self.members.get_or_404(member_id)
        if member.role_code == RoleCode.OWNER.value:
            raise AppError("所有者角色只能通过所有权转让修改", code=ErrorCode.LAST_ADMIN_PROTECTED)
        if member.role_code == RoleCode.ADMIN.value and role_code != RoleCode.ADMIN:
            await self._protect_last_admin()
        role = await self.roles.get_by_code(role_code.value)
        if role is None or not role.is_system:
            raise ParamInvalidError("角色不存在")
        before = member.role_code
        await self.members.update(member, role_code=role_code.value)
        await self.audit.append_action(
            tenant_id=tenant_id,
            action=AuditAction.MEMBER_ROLE_CHANGE,
            resource="member",
            user_id=actor_user_id,
            resource_id=member.id,
            before={"role_code": before},
            after={"role_code": role_code.value},
        )
        return await self._member_response(member)

    async def update_scope(
        self,
        member_id: int,
        payload: UpdateDataScopeRequest,
        *,
        tenant_id: int,
        actor_user_id: int,
    ) -> MemberResponse:
        member = await self.members.get_or_404(member_id)
        shop_ids = payload.shop_ids
        if payload.scope_type != int(DataScopeType.SELECTED):
            shop_ids = []
        elif not shop_ids:
            raise ParamInvalidError("SELECTED 数据范围至少需要一个店铺 ID")
        await self.scopes.upsert(
            tenant_user_id=member.id,
            resource_type=payload.resource_type,
            scope_type=payload.scope_type,
            shop_ids=shop_ids,
        )
        await self.audit.append_action(
            tenant_id=tenant_id,
            action=AuditAction.MEMBER_SCOPE_CHANGE,
            resource="member",
            user_id=actor_user_id,
            resource_id=member.id,
            after={
                "resource_type": payload.resource_type,
                "scope_type": payload.scope_type,
                "shop_ids": [str(item) for item in shop_ids],
            },
        )
        return await self._member_response(member)

    async def remove(self, member_id: int, *, tenant_id: int, actor_user_id: int) -> None:
        member = await self.members.get_or_404(member_id)
        if member.role_code == RoleCode.OWNER.value:
            raise AppError("不能移除租户所有者", code=ErrorCode.LAST_ADMIN_PROTECTED)
        if member.role_code == RoleCode.ADMIN.value:
            await self._protect_last_admin()
        await self.members.soft_delete(member)
        await self.audit.append_action(
            tenant_id=tenant_id,
            action=AuditAction.MEMBER_REMOVE,
            resource="member",
            user_id=actor_user_id,
            resource_id=member.id,
            before={"user_id": str(member.user_id), "role_code": member.role_code},
        )

    async def _protect_last_admin(self) -> None:
        if await self.members.count_active_admins() <= 1:
            raise AppError("至少保留一名所有者或管理员", code=ErrorCode.LAST_ADMIN_PROTECTED)

    async def _member_response(self, member: TenantUser) -> MemberResponse:
        user = await self.users.get(member.user_id)
        if user is None:
            raise NotFoundError("成员账号不存在")
        return MemberResponse(
            id=member.id,
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            role_code=RoleCode(member.role_code),
            status=int(member.status),
            joined_at=member.joined_at,
            data_scope=await self.scopes.list_with_shop_scope(member.id),
        )

    @staticmethod
    def _invitation_response(invitation: MemberInvitation) -> InvitationResponse:
        return InvitationResponse(
            id=invitation.id,
            email=invitation.email,
            role_code=RoleCode(invitation.role_code),
            expires_at=invitation.expires_at,
            status=int(invitation.status),
        )

    @staticmethod
    def _new_id() -> int:
        from app.db.snowflake import next_snowflake_id

        return next_snowflake_id()


__all__ = ["MemberService"]
