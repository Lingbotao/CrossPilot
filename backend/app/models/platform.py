"""平台与授权域（PRD 9.2 ②，Alembic 迁移批次 2）。

``platform`` 是全局字典，不带 tenant_id。
其余表继承 ``TenantMixin``。``sync_task`` 与 ``platform_api_log`` 是流水，禁止软删除。
``platform_api_log`` 按月分区，主键包含 ``created_at``。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, SoftDeleteMixin, TenantMixin
from app.db.snowflake import next_snowflake_id
from app.models.enums import ShopStatus


class Platform(Base, PKMixin, AuditMixin):
    """平台字典。运行时只读。"""

    __tablename__ = "platform"

    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default=text("1"))


class Shop(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """店铺。解绑走软删除，并保留 ``data_retain_until``。"""

    __tablename__ = "shop"

    platform_code: Mapped[str] = mapped_column(String(32), ForeignKey("platform.code"), nullable=False)
    site_code: Mapped[str] = mapped_column(String(8), nullable=False)
    shop_name: Mapped[str] = mapped_column(String(128), nullable=False)
    platform_shop_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=int(ShopStatus.ACTIVE))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    unbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_shop_platform_identity",
            "tenant_id",
            "platform_code",
            "site_code",
            "platform_shop_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_shop_tenant_id_status", "tenant_id", "status"),
    )


class ShopCredential(Base, PKMixin, TenantMixin, AuditMixin):
    """平台令牌。access/refresh 只存 AES-256-GCM 密文。"""

    __tablename__ = "shop_credential"

    shop_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shop.id"), nullable=False)
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    refresh_fail_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_refresh_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        UniqueConstraint("shop_id", name="uq_shop_credential_shop_id"),
        Index("ix_shop_credential_tenant_id_expires_at", "tenant_id", "expires_at"),
    )


class ShopGroup(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """店铺分组。分组功能是 P1，本表只随授权域一起建，不提供接口。"""

    __tablename__ = "shop_group"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    shop_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, default=list, server_default=text("'{}'::bigint[]")
    )

    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_shop_group_tenant_id_name"),)


class SyncTask(Base, PKMixin, TenantMixin, AuditMixin):
    """同步任务流水。禁止软删除。"""

    __tablename__ = "sync_task"

    shop_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shop.id"), nullable=False)
    module: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        Index("ix_sync_task_tenant_id_shop_id_created_at", "tenant_id", "shop_id", "created_at"),
        Index("ix_sync_task_tenant_id_status", "tenant_id", "status"),
    )


class PlatformApiLog(Base, TenantMixin):
    """平台调用日志，按月分区。"""

    __tablename__ = "platform_api_log"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=next_snowflake_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    http_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default=text("0"))
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at", name="pk_platform_api_log"),
        Index("ix_platform_api_log_tenant_id_created_at", "tenant_id", "created_at"),
        Index("ix_platform_api_log_tenant_id_shop_id_created_at", "tenant_id", "shop_id", "created_at"),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )


__all__ = [
    "Platform",
    "PlatformApiLog",
    "Shop",
    "ShopCredential",
    "ShopGroup",
    "SyncTask",
]
