"""内置角色矩阵。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import DbSession, Identity, require_permission
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.member import RoleResponse
from app.services.member_service import MemberService

router = APIRouter(prefix="/roles", tags=["成员与权限"])
RoleReader = Annotated[Identity, Depends(require_permission(Perm.MEMBER_READ))]


@router.get("", response_model=ApiResponse[list[RoleResponse]], summary="内置角色列表")
async def list_roles(_identity: RoleReader, session: DbSession) -> ApiResponse[list[RoleResponse]]:
    return ok(await MemberService(session).list_roles())


__all__ = ["router"]
