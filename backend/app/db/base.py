"""SQLAlchemy 2.0 声明式基类与通用 mixin（PRD 9.1）。

四条硬约定（映射到代码）：
| PRD 原则        | 落地方式                                    |
|-----------------|---------------------------------------------|
| 主键 BIGINT     | ``PKMixin``（Snowflake，非自增）            |
| 表必须含 tenant | ``TenantMixin``（NOT NULL + 索引）          |
| 审计字段        | ``AuditMixin``（created_at/updated_at/by）  |
| 软删除          | ``SoftDeleteMixin``（deleted_at，仅主数据） |

另外：约束/索引**统一命名**，否则 Alembic 自动生成的迁移在 drop 时会找不到目标名。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.snowflake import next_snowflake_id

# 命名约定：没有它，autogenerate 出的迁移在 downgrade / drop constraint 时会因匿名约束报错
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def to_dict(self, exclude: set[str] | None = None) -> dict[str, Any]:
        skip = exclude or set()
        return {c.name: getattr(self, c.name) for c in self.__table__.columns if c.name not in skip}

    def __repr__(self) -> str:
        pk = getattr(self, "id", None)
        return f"<{self.__class__.__name__} id={pk}>"


class PKMixin:
    """主键：BIGINT + Snowflake（不可枚举）。"""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, default=next_snowflake_id)


class TenantMixin:
    """租户隔离字段。

    ⚠️ **所有业务表都必须继承本 mixin**。``app.db.tenant_filter`` 与 RLS 策略
    都按「表里有没有 tenant_id 列」来判断是否需要隔离 —— 漏继承 = 漏隔离。
    """

    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True, comment="租户 ID，隔离第一关键字")


class AuditMixin:
    """审计字段（PRD 9.1：所有表都要有）。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class SoftDeleteMixin:
    """软删除。**只用于业务主数据**（商品、店铺、供应商…）。

    流水类表（订单、库存流水、审计日志）**严禁软删除** —— 删了就查不到，等于账不平。
    查询时通过 ``app.repositories.base.SoftDeleteQueryMixin`` 自动过滤。
    """

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


# 便于模型文件统一 import
__all__ = ["NAMING_CONVENTION", "AuditMixin", "Base", "PKMixin", "SoftDeleteMixin", "TenantMixin"]
