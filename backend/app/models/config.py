"""系统配置域（PRD 9.2 ⑩）里本里程碑用到的表。

``platform_status_mapping`` 与 ``platform_rate_limit`` 都是平台字典，不带 ``tenant_id``。
改一行就能换映射或配额，不必发版。
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin
from app.engines.order_status import UNIFIED_STATUS_SQL


class PlatformStatusMapping(Base, PKMixin, AuditMixin):
    """平台原文状态 → 统一九态。同一平台同一原文只保留一行，后写的覆盖先写的。"""

    __tablename__ = "platform_status_mapping"

    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_status: Mapped[str] = mapped_column(String(64), nullable=False)
    unified_status: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "platform_code",
            "platform_status",
            name="uq_platform_status_mapping_platform_code_platform_status",
        ),
        CheckConstraint(
            f"unified_status IN ({UNIFIED_STATUS_SQL})",
            name="unified_status",
        ),
        Index("ix_platform_status_mapping_platform_code", "platform_code"),
    )


class PlatformRateLimit(Base, PKMixin, AuditMixin):
    """一个平台一行配额。没有行时同步仍用 ``quotas.py`` 的默认值。"""

    __tablename__ = "platform_rate_limit"

    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)
    qps: Mapped[int] = mapped_column(Integer, nullable=False)
    burst: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 库存回传滞后阈值。没配行时用 quotas.DEFAULT_STOCK_PUSH_LAG_SECONDS，不在推送服务里写死。
    stock_push_lag_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600, server_default="600")

    __table_args__ = (
        UniqueConstraint("platform_code", name="uq_platform_rate_limit_platform_code"),
        CheckConstraint(
            "dimension IN ('shop', 'per_shop', 'app', 'per_app')",
            name="dimension",
        ),
        CheckConstraint("qps > 0", name="qps"),
        CheckConstraint("burst > 0", name="burst"),
        CheckConstraint("batch_limit > 0", name="batch_limit"),
        CheckConstraint("concurrency IS NULL OR concurrency > 0", name="concurrency"),
        CheckConstraint("daily_quota IS NULL OR daily_quota > 0", name="daily_quota"),
        CheckConstraint("stock_push_lag_seconds > 0", name="stock_push_lag_seconds"),
    )


__all__ = ["PlatformRateLimit", "PlatformStatusMapping"]
