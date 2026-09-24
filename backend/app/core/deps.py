"""FastAPI 依赖注入：身份、租户上下文、权限校验。

**这里执行顺序是整个鉴权链的骨架**（顺序错了 RLS 会读到空上下文）：

    Bearer Token → 解析签名 → 写 contextvar（tenant/user/role）
        → 绑定 RLS 会话变量 → 查库校验成员关系 → 交给端点

请务必注意：``get_identity`` 里"先写上下文、再绑 RLS、最后才查库"这三步
不能调换，也不能把查库动作提到前面。
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_role_code, set_tenant_id, set_user_id
from app.core.errors import (
    AccountLockedError,
    AppError,
    ErrorCode,
    PermissionDeniedError,
    TenantDisabledError,
    UnauthenticatedError,
)
from app.core.logging import get_logger
from app.core.permissions import Perm, permissions_for_role, role_can_view_cost
from app.core.security import TokenPayload, consume_confirmation_token, decode_token
from app.core.token_blacklist import assert_token_not_revoked
from app.db.session import apply_rls_tenant, get_db
from app.models.enums import TenantStatus, TenantUserStatus, UserStatus
from app.models.tenant import SysUser, Tenant, TenantUser
from app.repositories.identity import SysUserRepository, TenantRepository, TenantUserRepository

log = get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False, description="Bearer <access_token>")

DbSession = Annotated[AsyncSession, Depends(get_db)]


@dataclass(frozen=True, slots=True)
class Identity:
    """当前请求的身份三元组：用户 + 租户 + 成员关系。"""

    user: SysUser
    tenant: Tenant
    membership: TenantUser
    payload: TokenPayload

    @property
    def role_code(self) -> str:
        return self.membership.role_code

    @property
    def permissions(self) -> frozenset[Perm]:
        return permissions_for_role(self.membership.role_code)

    @property
    def can_view_cost(self) -> bool:
        """★ F4：成本与利润可见性。"""
        return role_can_view_cost(self.membership.role_code)

    def has(self, *permissions: Perm) -> bool:
        """任一权限点命中即通过（用于"读或写"这类组合场景）。"""
        return bool(self.permissions & set(permissions))


async def get_identity(
    request: Request,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> Identity:
    if credentials is None or not credentials.credentials:
        raise UnauthenticatedError()

    payload = decode_token(credentials.credentials, expected_type="access")
    await assert_token_not_revoked(payload)
    if payload.tid is None:
        raise UnauthenticatedError("令牌缺少租户信息")

    # ---- 第 1 步：写上下文（后续所有日志与 ORM 过滤都依赖它）----
    set_tenant_id(payload.tid)
    set_user_id(payload.sub)
    set_role_code(payload.role)

    # ---- 第 2 步：绑定 RLS（必须在任何租户表查询之前）----
    await apply_rls_tenant(session, payload.tid)

    # ---- 第 3 步：查库校验（此时 ORM 过滤与 RLS 都已生效）----
    tenant = await TenantRepository(session).get(payload.tid)
    if tenant is None:
        raise UnauthenticatedError("租户不存在")
    if int(tenant.status) == int(TenantStatus.DISABLED):
        raise TenantDisabledError()

    membership = await TenantUserRepository(session).get_membership(payload.sub)
    if membership is None or int(membership.status) != int(TenantUserStatus.ACTIVE):
        raise UnauthenticatedError("成员关系已失效")

    user = await SysUserRepository(session).get(payload.sub)
    if user is None or int(user.status) != int(UserStatus.ACTIVE):
        raise UnauthenticatedError("账号不可用")
    if SysUserRepository.is_locked(user):
        raise AccountLockedError()

    # 角色可能在令牌签发后被管理员改过 —— 以数据库当前值为准，不用令牌里的旧值
    if membership.role_code != payload.role:
        set_role_code(membership.role_code)
        log.info(
            "role_changed_since_token_issued",
            user_id=user.id,
            token_role=payload.role,
            current_role=membership.role_code,
        )

    return Identity(user=user, tenant=tenant, membership=membership, payload=payload)


CurrentIdentity = Annotated[Identity, Depends(get_identity)]


def require_permission(*permissions: Perm) -> Callable[..., Coroutine[Any, Any, Identity]]:
    """权限校验依赖工厂。用法：``identity: Identity = Depends(require_permission(Perm.ORDER_READ))``。

    铁律：**权限校验必须在后端接口层强制执行**，前端隐藏不作为安全边界（PRD 约束）。
    """

    async def _dependency(identity: CurrentIdentity) -> Identity:
        if not identity.has(*permissions):
            required = ", ".join(p.value for p in permissions)
            log.warning(
                "permission_denied",
                user_id=identity.user.id,
                role=identity.role_code,
                required=required,
            )
            raise PermissionDeniedError(f"缺少权限：{required}")
        return identity

    return _dependency


def require_confirmation(action: str) -> Callable[..., Coroutine[Any, Any, Identity]]:
    """要求一次性二次确认令牌，且绑定当前用户、租户与动作。"""

    async def _dependency(
        identity: CurrentIdentity,
        confirmation_token: Annotated[str | None, Header(alias="X-Confirmation-Token")] = None,
    ) -> Identity:
        if not confirmation_token:
            raise AppError("该操作需要二次确认", code=ErrorCode.CONFIRMATION_REQUIRED)
        await consume_confirmation_token(
            confirmation_token,
            action=action,
            user_id=identity.user.id,
            tenant_id=identity.tenant.id,
        )
        return identity

    return _dependency


async def require_cost_visibility(identity: CurrentIdentity) -> Identity:
    """★ F4 硬规则：成本价/利润数据的访问兜底。

    即使某个接口忘了走 ``require_permission(Perm.COST_READ)``，
    只要挂在成本类接口上，就仍然挡得住 OPS_STAFF。
    """
    if not identity.can_view_cost:
        log.warning("cost_visibility_denied", user_id=identity.user.id, role=identity.role_code)
        raise PermissionDeniedError("无权查看成本与利润数据")
    return identity


def current_tenant_id(identity: CurrentIdentity) -> int:
    return int(identity.tenant.id)


__all__ = [
    "CurrentIdentity",
    "DbSession",
    "Identity",
    "bearer_scheme",
    "current_tenant_id",
    "get_identity",
    "require_cost_visibility",
    "require_confirmation",
    "require_permission",
]
