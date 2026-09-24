"""租户与权限域 8 张表（PRD 9.2 ①，Alembic 迁移批次 1）。

表清单：``tenant`` / ``sys_user`` / ``tenant_user`` / ``role`` /
``user_data_scope`` / ``member_invitation`` / ``audit_log`` / ``login_log``
（后两张在 ``app/models/audit.py``）

设计要点：
1. ``tenant`` 与 ``sys_user`` **不带 tenant_id** —— 它们是租户体系本身。
   用户是跨租户的全局身份，通过 ``tenant_user`` 关联到多个租户。
2. 其余表全部继承 ``TenantMixin``，自动获得 ORM 过滤 + RLS 双重保护。
3. 系统角色（8 个内置角色）**按租户 seed 一份**，``is_system=True`` 只读；
   这样角色列表查询天然受租户隔离，不需要为"全局角色"开 RLS 特例。
4. 索引首列一律为 ``tenant_id``（PRD 6 章批次规则 2）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.permissions import RoleCode
from app.db.base import AuditMixin, Base, PKMixin, SoftDeleteMixin, TenantMixin
from app.models.enums import (
    DataScopeType,
    InvitationStatus,
    TenantPlan,
    TenantStatus,
    TenantUserStatus,
    UserStatus,
)


class Tenant(Base, PKMixin, AuditMixin):
    """租户（企业客户）。这是隔离的根，本身不带 tenant_id。"""

    __tablename__ = "tenant"

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="租户唯一标识（登录/URL 用）")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="企业名称")
    plan: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=int(TenantPlan.TRIAL))
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=int(TenantStatus.ACTIVE), index=True)
    default_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="CNY", comment="ISO 4217")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    contact_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_tenant_status_plan", "status", "plan"),)


class SysUser(Base, PKMixin, AuditMixin):
    """全局用户身份。一个用户可属于多个租户，故本表无 tenant_id。"""

    __tablename__ = "sys_user"

    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False, comment="bcrypt(SHA256(pwd))")
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=int(UserStatus.ACTIVE))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 登录失败锁定（PRD F1-01）
    failed_login_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 密码修改后使既有 Refresh Token 全部失效
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantUser(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """租户成员关系（PRD A-04）。"""

    __tablename__ = "tenant_user"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id", ondelete="CASCADE"), nullable=False)
    role_code: Mapped[str] = mapped_column(String(32), nullable=False, comment="角色码，见 RoleCode")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=int(TenantUserStatus.INVITED))
    nickname: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="租户内别名")
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_user_tenant_id_user_id"),
        Index("ix_tenant_user_tenant_id_status", "tenant_id", "status"),
        Index("ix_tenant_user_user_id", "user_id"),
    )


class Role(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """角色。

    内置 8 个角色在租户注册时 seed，``is_system=True`` 不可改不可删；
    租户可自建自定义角色（``is_system=False``），权限点在 ``permission_codes`` 里。
    """

    __tablename__ = "role"

    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"), comment="系统内置角色，只读"
    )
    permission_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"), comment="权限点列表"
    )
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_role_tenant_id_code"),
        Index("ix_role_tenant_id_is_system", "tenant_id", "is_system"),
    )

    @property
    def is_default_role(self) -> bool:
        """是否为内置角色（值取自代码常量，而非数据库里可被篡改的字段）。"""
        return self.code in {r.value for r in RoleCode}


class UserDataScope(Base, PKMixin, TenantMixin, AuditMixin):
    """成员数据范围（PRD A-05）。

    ``scope_type=SELECTED`` 时生效于 ``shop_ids``；
    ``ALL`` 时忽略列表；``NONE`` 时该资源一律不可见。
    """

    __tablename__ = "user_data_scope"

    tenant_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tenant_user.id", ondelete="CASCADE"), nullable=False
    )
    resource_type: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="见 ResourceType")
    scope_type: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=int(DataScopeType.ALL))
    shop_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, server_default=text("'{}'"), comment="scope_type=SELECTED 时的店铺白名单"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "tenant_user_id", "resource_type", name="uq_user_data_scope_scope"),
        Index("ix_user_data_scope_tenant_id_tenant_user_id", "tenant_id", "tenant_user_id"),
    )


class MemberInvitation(Base, PKMixin, TenantMixin, AuditMixin):
    """成员邀请（PRD A-04，令牌 72 小时有效）。"""

    __tablename__ = "member_invitation"

    email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    role_code: Mapped[str] = mapped_column(String(32), nullable=False)
    # 只存哈希：明文令牌仅出现在邀请邮件里，库被拖走也无法直接使用
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=int(InvitationStatus.PENDING))
    invited_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_member_invitation_tenant_id_status", "tenant_id", "status"),
        Index("ix_member_invitation_expires_at", "expires_at"),
    )


__all__ = ["MemberInvitation", "Role", "SysUser", "Tenant", "TenantUser", "UserDataScope"]
