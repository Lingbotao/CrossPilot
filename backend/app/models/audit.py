"""审计与登录日志（PRD 9.2 ①，Alembic 迁移批次 1）。

⚠️ **分区表必须在第一次建表时就分区**（PRD 风险 R12）：
   事后分区等于停服重建 —— ``audit_log`` 与 ``login_log`` 一旦有历史数据，
   再想改成按月分区，只能导数据+改表名+回灌。所以这里直接按 RANGE(created_at) 分区。

⚠️ **流水表禁止软删除**：审计日志删了就查不到，等于审计失效。
    保留策略是「归档到冷存储」（PRD 9.4：超 24 个月归档），不是删除。

分区表的两个硬约束（PostgreSQL 规定）：
1. 主键必须包含分区键 → ``PRIMARY KEY (id, created_at)``，不能只用 id；
2. 唯一索引必须包含分区键 → ``token_hash`` 之类的全局唯一约束不能建在分区表上。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Self

from sqlalchemy import BigInteger, DateTime, Index, PrimaryKeyConstraint, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TenantMixin
from app.db.snowflake import next_snowflake_id
from app.models.enums import LoginResult


class AuditLog(Base, TenantMixin):
    """审计日志（按月分区）。

    留痕范围（PRD A-07）：登录、授权、批量操作、删除、导出 —— 五类必留。
    """

    __tablename__ = "audit_log"

    # 分区表主键必须带分区键，因此这里不能用 PKMixin
    id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=next_snowflake_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, comment="见 AuditAction")
    resource: Mapped[str] = mapped_column(String(64), nullable=False, comment="资源类型，如 shop/order")
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    ua: Mapped[str | None] = mapped_column(String(512), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(26), nullable=True, comment="trace_id，便于与应用日志对齐")
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at", name="pk_audit_log"),
        Index("ix_audit_log_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_audit_log_tenant_id_action_created_at", "tenant_id", "action", "created_at"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )

    @classmethod
    def build(
        cls,
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
        request_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Self:
        return cls(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            resource=resource,
            resource_id=str(resource_id) if resource_id is not None else None,
            before=before,
            after=after,
            ip=ip,
            ua=ua,
            request_id=request_id,
            extra=extra,
        )


class LoginLog(Base, PKMixin):
    """登录日志（PRD 9.2 ①）。

    不分区：单租户登录量远小于审计量，分区收益不抵复杂度；
    数据量上来后（>5000 万行）再迁移，成本可接受。

    ⚠️ **本表刻意不继承 ``TenantMixin``**，``tenant_id`` 允许为空。原因：
        登录尝试发生在租户上下文建立**之前** —— 用不存在的邮箱尝试登录时，
        根本没有租户可以归属。强行要求 NOT NULL 会导致这类爆破尝试无法记录。

    由此带来两个必须知道的后果：
    1. **ORM 不会自动注入租户过滤**，``LoginLogRepository`` 的所有查询
       必须显式带 ``tenant_id`` 条件（这是全项目唯一的例外，已在此说明）；
    2. **RLS 策略仍然生效**：策略比较 ``tenant_id = <当前租户>``，
       而 NULL 与任何值比较都为 false —— 所以"未归属租户"的尝试
       对所有租户管理员都不可见，只有平台侧能看。这正是我们要的效果。
    """

    __tablename__ = "login_log"

    tenant_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True, comment="可为空：未知邮箱的登录尝试不归属任何租户"
    )
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, comment="登录尝试的邮箱（便于排查爆破）")
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    ua: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=int(LoginResult.FAILURE))
    fail_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_login_log_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_login_log_email_created_at", "email", "created_at"),
    )

    @classmethod
    def build(
        cls,
        *,
        tenant_id: int | None,
        result: LoginResult,
        user_id: int | None = None,
        email: str | None = None,
        ip: str | None = None,
        ua: str | None = None,
        fail_reason: str | None = None,
    ) -> Self:
        return cls(
            tenant_id=tenant_id,
            user_id=user_id,
            email=email,
            ip=ip,
            ua=ua,
            result=int(result),
            fail_reason=fail_reason,
        )


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = ["AuditLog", "LoginLog", "utcnow"]
