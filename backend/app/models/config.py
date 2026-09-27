"""系统配置域（PRD 9.2 ⑩）里本里程碑用到的表。

``platform_status_mapping`` 是平台字典，不带 ``tenant_id``。
改一行就能把新的平台原文映到九态，不必发版。
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, String, UniqueConstraint
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


__all__ = ["PlatformStatusMapping"]
