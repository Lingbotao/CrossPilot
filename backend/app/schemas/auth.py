"""认证与租户相关契约。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_serializer

from app.core.permissions import RoleCode
from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    tenant_code: str | None = Field(
        default=None,
        max_length=64,
        description="租户标识。用户只属于一个租户时可省略；属于多个租户时必填",
    )


class TenantBrief(BaseModel):
    id: int
    code: str
    name: str
    role_code: RoleCode

    @field_serializer("id")
    def _ser(self, v: int) -> str:
        return str(v)


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = Field(description="Access Token 剩余秒数")
    tenant: TenantBrief
    available_tenants: list[TenantBrief] = Field(
        default_factory=list, description="该用户可进入的全部租户，前端用于租户切换"
    )


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class CurrentUserResponse(ORMModel):
    id: int
    email: EmailStr
    display_name: str | None = None
    avatar_url: str | None = None
    last_login_at: datetime | None = None

    @field_serializer("id")
    def _ser(self, v: int) -> str:
        return str(v)


class TenantCurrentResponse(BaseModel):
    """``GET /tenants/current`` —— 前端启动时拉一次，用于渲染权限与数据范围。"""

    id: int
    code: str
    name: str
    plan: int
    status: int
    default_currency: str
    timezone: str
    role_code: RoleCode
    role_name: str = Field(description="角色中文名")
    permissions: list[str] = Field(description="权限点列表，前端 PermissionGuard 按此渲染")
    can_view_cost: bool = Field(description="★ F4：是否可见采购成本价与利润")
    data_scope: dict[str, object] = Field(
        default_factory=dict, description="数据范围：{resource_type: {scope_type, shop_ids}}"
    )

    @field_serializer("id")
    def _ser(self, v: int) -> str:
        return str(v)


class MeResponse(BaseModel):
    user: CurrentUserResponse
    tenant: TenantCurrentResponse
