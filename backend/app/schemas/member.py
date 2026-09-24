"""成员、角色与数据范围契约。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_serializer, field_validator

from app.core.pagination import PageInfo
from app.core.permissions import RoleCode


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role_code: RoleCode

    @field_validator("role_code")
    @classmethod
    def _owner_cannot_be_invited(cls, value: RoleCode) -> RoleCode:
        if value == RoleCode.OWNER:
            raise ValueError("不能通过邀请新增所有者")
        return value


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=20, max_length=4096)
    password: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)


class UpdateMemberRoleRequest(BaseModel):
    role_code: RoleCode

    @field_validator("role_code")
    @classmethod
    def _owner_is_transfer_only(cls, value: RoleCode) -> RoleCode:
        if value == RoleCode.OWNER:
            raise ValueError("所有权变更必须走独立转让流程")
        return value


class UpdateDataScopeRequest(BaseModel):
    resource_type: int = Field(ge=1, le=3)
    scope_type: int = Field(ge=1, le=3)
    shop_ids: list[int] = Field(default_factory=list, max_length=500)

    @field_validator("shop_ids")
    @classmethod
    def _unique_positive_ids(cls, value: list[int]) -> list[int]:
        if any(item <= 0 for item in value):
            raise ValueError("shop_ids 必须为正整数")
        return list(dict.fromkeys(value))


class MemberResponse(BaseModel):
    id: int
    user_id: int
    email: EmailStr
    display_name: str | None
    role_code: RoleCode
    status: int
    joined_at: datetime | None
    data_scope: dict[str, object] = Field(default_factory=dict)

    @field_serializer("id", "user_id")
    def _serialize_id(self, value: int) -> str:
        return str(value)


class InvitationResponse(BaseModel):
    id: int
    email: EmailStr
    role_code: RoleCode
    expires_at: datetime
    status: int

    @field_serializer("id")
    def _serialize_id(self, value: int) -> str:
        return str(value)


class MemberListResponse(BaseModel):
    members: list[MemberResponse]
    invitations: list[InvitationResponse]
    page_info: PageInfo


class RoleResponse(BaseModel):
    code: RoleCode
    name: str
    is_system: bool
    permission_codes: list[str]
    description: str | None = None


class AcceptedInvitationResponse(BaseModel):
    tenant_id: int
    member_id: int

    @field_serializer("tenant_id", "member_id")
    def _serialize_id(self, value: int) -> str:
        return str(value)


__all__ = [
    "AcceptInvitationRequest",
    "AcceptedInvitationResponse",
    "InvitationResponse",
    "InviteMemberRequest",
    "MemberResponse",
    "MemberListResponse",
    "RoleResponse",
    "UpdateDataScopeRequest",
    "UpdateMemberRoleRequest",
]
