"""成员邀请、角色与数据范围接口。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbSession, Identity, require_confirmation, require_permission
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.member import (
    AcceptedInvitationResponse,
    AcceptInvitationRequest,
    InvitationResponse,
    InviteMemberRequest,
    MemberListResponse,
    MemberResponse,
    UpdateDataScopeRequest,
    UpdateMemberRoleRequest,
)
from app.services.member_service import MemberService

router = APIRouter(prefix="/members", tags=["成员与权限"])

MemberReader = Annotated[Identity, Depends(require_permission(Perm.MEMBER_READ))]
MemberWriter = Annotated[Identity, Depends(require_permission(Perm.MEMBER_WRITE))]


@router.get("", response_model=ApiResponse[MemberListResponse], summary="成员列表")
async def list_members(
    identity: MemberReader,
    session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> ApiResponse[MemberListResponse]:
    return ok(await MemberService(session).list_members(page=page, page_size=page_size))


@router.post("/invite", response_model=ApiResponse[InvitationResponse], summary="邀请成员")
async def invite_member(
    payload: InviteMemberRequest, identity: MemberWriter, session: DbSession
) -> ApiResponse[InvitationResponse]:
    result = await MemberService(session).invite(payload, tenant_id=identity.tenant.id, actor_user_id=identity.user.id)
    return ok(result, message="邀请已创建")


@router.post(
    "/invitations/accept",
    response_model=ApiResponse[AcceptedInvitationResponse],
    summary="接受成员邀请",
)
async def accept_invitation(
    payload: AcceptInvitationRequest, session: DbSession
) -> ApiResponse[AcceptedInvitationResponse]:
    result = await MemberService(session).accept(payload.token, payload.password, payload.display_name)
    return ok(result, message="已加入租户")


@router.post(
    "/invitations/{invitation_id}/revoke",
    response_model=ApiResponse[dict[str, str]],
    summary="撤销邀请",
)
async def revoke_invitation(
    invitation_id: int, identity: MemberWriter, session: DbSession
) -> ApiResponse[dict[str, str]]:
    await MemberService(session).revoke_invitation(
        invitation_id,
        tenant_id=identity.tenant.id,
        actor_user_id=identity.user.id,
    )
    return ok({"status": "revoked"}, message="邀请已撤销")


@router.patch(
    "/{member_id}/role",
    response_model=ApiResponse[MemberResponse],
    summary="修改成员角色",
)
async def update_member_role(
    member_id: int,
    payload: UpdateMemberRoleRequest,
    identity: MemberWriter,
    session: DbSession,
) -> ApiResponse[MemberResponse]:
    result = await MemberService(session).change_role(
        member_id,
        payload.role_code,
        tenant_id=identity.tenant.id,
        actor_user_id=identity.user.id,
    )
    return ok(result, message="角色已更新")


@router.patch(
    "/{member_id}/data-scope",
    response_model=ApiResponse[MemberResponse],
    summary="修改成员数据范围",
)
async def update_member_scope(
    member_id: int,
    payload: UpdateDataScopeRequest,
    identity: MemberWriter,
    session: DbSession,
) -> ApiResponse[MemberResponse]:
    result = await MemberService(session).update_scope(
        member_id,
        payload,
        tenant_id=identity.tenant.id,
        actor_user_id=identity.user.id,
    )
    return ok(result, message="数据范围已更新")


@router.delete(
    "/{member_id}",
    response_model=ApiResponse[dict[str, str]],
    summary="移除成员",
)
async def remove_member(
    member_id: int,
    identity: MemberWriter,
    session: DbSession,
    _confirmed: Annotated[Identity, Depends(require_confirmation("member.remove"))],
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[dict[str, str]]:
    await MemberService(session).remove(
        member_id,
        tenant_id=identity.tenant.id,
        actor_user_id=identity.user.id,
    )
    return ok({"status": "removed"}, message="成员已移除")


__all__ = ["router"]
