"""身份与租户相关 Repository。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update

from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.models.audit import AuditLog, LoginLog
from app.models.enums import TenantUserStatus
from app.models.tenant import MemberInvitation, Role, SysUser, Tenant, TenantUser, UserDataScope
from app.repositories.base import BaseRepository

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TenantMembership:
    """跨租户查询成员关系的只读结果（登录阶段使用）。"""

    tenant_id: int
    code: str
    name: str
    role_code: str
    status: int


class TenantRepository(BaseRepository[Tenant]):
    """租户表本身不带 tenant_id，故不受 ORM 自动过滤影响（这是隔离的根）。"""

    model = Tenant

    async def get_by_code(self, code: str) -> Tenant | None:
        return await self.get_by(code=code)


class SysUserRepository(BaseRepository[SysUser]):
    """全局用户。不带 tenant_id —— 用户身份跨租户。"""

    model = SysUser

    async def get_by_email(self, email: str) -> SysUser | None:
        return await self.get_by(email=email.lower().strip())

    async def record_login_failure(self, user: SysUser) -> None:
        """累加失败次数，达阈值即锁定（PRD F1-01）。"""
        count = int(user.failed_login_count) + 1
        values: dict[str, Any] = {"failed_login_count": count}
        if count >= settings.login_max_failures:
            values["locked_until"] = datetime.now(UTC) + timedelta(minutes=settings.login_lock_minutes)
            log.warning("account_locked", user_id=user.id, failures=count)
        await self.update(user, **values)

    async def record_login_success(self, user: SysUser) -> None:
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = datetime.now(UTC)
        await self.update(user, failed_login_count=0, locked_until=None, last_login_at=user.last_login_at)

    @staticmethod
    def is_locked(user: SysUser) -> bool:
        return user.locked_until is not None and user.locked_until > datetime.now(UTC)


class TenantUserRepository(BaseRepository[TenantUser]):
    model = TenantUser

    async def get_membership(self, user_id: int) -> TenantUser | None:
        """在当前租户上下文内取成员关系（依赖 ORM 自动注入 tenant 条件）。"""
        stmt = self.base_select().where(TenantUser.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_active_memberships(self, user_id: int) -> list[TenantUser]:
        stmt = (
            self.base_select()
            .where(TenantUser.user_id == user_id, TenantUser.status == int(TenantUserStatus.ACTIVE))
            .order_by(TenantUser.created_at.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_user_tenants_cross_tenant(self, user_id: int) -> list[TenantMembership]:
        """★ 登录阶段专用：跨租户枚举用户所属租户。

        为什么必须走数据库函数（``app_user_tenants``）：
            此刻还没有租户上下文，RLS 会把 ``tenant_user`` 全部挡掉（默认拒绝）。
            用普通 SQL 绕不过去，而给应用角色开 ``BYPASSRLS`` 等于废掉第二道防线。
            所以用一个 **SECURITY DEFINER** 函数把"这一个查询"的越权范围
            收敛到数据库内部：它只能按 user_id 查成员关系，不能查别的。

        注意：这里返回的是**用户自己的**成员关系，不构成跨租户数据泄露。
        """
        result = await self.session.execute(
            text("SELECT tenant_id, code, name, role_code, status FROM app_user_tenants(:uid)"),
            {"uid": user_id},
        )
        return [
            TenantMembership(
                tenant_id=int(row.tenant_id),
                code=str(row.code),
                name=str(row.name),
                role_code=str(row.role_code),
                status=int(row.status),
            )
            for row in result
        ]

    async def count_active_admins(self) -> int:
        """统计当前租户内 OWNER/ADMIN 数量 —— 防止"删掉最后一个管理员"。"""
        stmt = (
            select(TenantUser.id)
            .where(
                TenantUser.role_code.in_(["OWNER", "ADMIN"]),
                TenantUser.status == int(TenantUserStatus.ACTIVE),
                TenantUser.deleted_at.is_(None),
            )
            .limit(2)
        )
        return len(list((await self.session.execute(stmt)).scalars().all()))


class RoleRepository(BaseRepository[Role]):
    model = Role

    async def list_for_tenant(self) -> list[Role]:
        stmt = self.base_select().order_by(Role.is_system.desc(), Role.code.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_code(self, code: str) -> Role | None:
        return await self.get_by(code=code)


class UserDataScopeRepository(BaseRepository[UserDataScope]):
    model = UserDataScope

    async def list_by_member(self, tenant_user_id: int) -> list[UserDataScope]:
        stmt = self.base_select().where(UserDataScope.tenant_user_id == tenant_user_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_with_shop_scope(self, tenant_user_id: int) -> dict[str, Any]:
        """返回前端可直接消费的数据范围结构。"""
        rows = await self.list_by_member(tenant_user_id)
        return {
            str(row.resource_type): {
                "scope_type": int(row.scope_type),
                "shop_ids": [str(i) for i in (row.shop_ids or [])],
            }
            for row in rows
        }


class MemberInvitationRepository(BaseRepository[MemberInvitation]):
    model = MemberInvitation

    async def get_pending_by_hash(self, token_hash: str) -> MemberInvitation | None:
        stmt = (
            select(MemberInvitation)
            .where(
                MemberInvitation.token_hash == token_hash,
                MemberInvitation.expires_at > datetime.now(UTC),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def revoke_pending_for_email(self, email: str) -> int:
        """重发邀请时先作废旧的，避免一个邮箱同时有多个有效令牌。"""
        stmt = (
            update(MemberInvitation)
            .where(
                MemberInvitation.email == email.lower().strip(),
                MemberInvitation.expires_at > datetime.now(UTC),
            )
            .values(expires_at=datetime.now(UTC))
        )
        result = await self.session.execute(stmt)
        # Core DML 返回的是 CursorResult（带 rowcount）；Result 泛型上没有该属性
        return int(getattr(result, "rowcount", 0) or 0)


class LoginLogRepository(BaseRepository[LoginLog]):
    """⚠️ ``login_log`` 不继承 TenantMixin（登录发生在租户上下文之前，见模型注释），
    因此**所有查询必须显式带 tenant_id 条件** —— 这是全项目唯一的例外。

    下面的 ``_scoped`` 把这条约束收在一处，新增查询方法时必须经过它。
    """

    model = LoginLog

    def _scoped(self) -> Any:
        from app.core.context import require_tenant_id

        return select(LoginLog).where(LoginLog.tenant_id == require_tenant_id())

    async def append(self, log_row: LoginLog) -> LoginLog:
        self.session.add(log_row)
        await self.session.flush()
        return log_row

    async def recent_failures(self, email: str, minutes: int = 15) -> int:
        """统计窗口内失败次数（用于爆破检测）。走全局计数：同一邮箱的尝试可能跨租户。"""
        from sqlalchemy import func

        since = datetime.now(UTC) - timedelta(minutes=minutes)
        stmt = (
            select(func.count())
            .select_from(LoginLog)
            .where(LoginLog.email == email.lower().strip(), LoginLog.created_at >= since, LoginLog.result == 2)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_for_current_tenant(self, limit: int = 50) -> list[LoginLog]:
        stmt = self._scoped().order_by(LoginLog.created_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())


class AuditLogRepository(BaseRepository[AuditLog]):
    model = AuditLog

    async def append(self, row: AuditLog) -> AuditLog:
        return await self.add(row)

    async def append_action(
        self,
        *,
        tenant_id: int,
        action: str,
        resource: str,
        user_id: int | None = None,
        resource_id: str | int | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip: str | None = None,
        ua: str | None = None,
    ) -> AuditLog:
        from app.core.context import get_trace_id

        row = AuditLog.build(
            tenant_id=tenant_id,
            action=action,
            resource=resource,
            user_id=user_id,
            resource_id=resource_id,
            before=before,
            after=after,
            ip=ip,
            ua=ua,
            request_id=get_trace_id(),
        )
        return await self.add(row)

    async def get_or_404(self, entity_id: int) -> AuditLog:
        # 分区表主键为 (id, created_at)，这里按 id 查即可（雪花 ID 全局唯一）
        obj = await self.get(entity_id)
        if obj is None:
            raise NotFoundError("审计日志不存在")
        return obj


__all__ = [
    "AuditLogRepository",
    "LoginLogRepository",
    "MemberInvitationRepository",
    "RoleRepository",
    "SysUserRepository",
    "TenantMembership",
    "TenantRepository",
    "TenantUserRepository",
    "UserDataScopeRepository",
]
