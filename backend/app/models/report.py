"""报表导出任务。文件在对象存储里，这张表只记状态，不能删除。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, TenantMixin
from app.engines.report import EXPORT_DATASETS, EXPORT_FORMATS, EXPORT_STATUSES

_DATASET_SQL = ", ".join(f"'{item}'" for item in EXPORT_DATASETS)
_FORMAT_SQL = ", ".join(f"'{item}'" for item in EXPORT_FORMATS)
_STATUS_SQL = ", ".join(f"'{item}'" for item in EXPORT_STATUSES)


class ReportExport(Base, PKMixin, TenantMixin, AuditMixin):
    """一次异步导出。同一租户同时只能有一个进行中的任务。"""

    __tablename__ = "report_export"

    dataset: Mapped[str] = mapped_column(String(32), nullable=False)
    file_format: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    include_cost: Mapped[bool] = mapped_column(Boolean, nullable=False)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        CheckConstraint(f"dataset IN ({_DATASET_SQL})", name="dataset"),
        CheckConstraint(f"file_format IN ({_FORMAT_SQL})", name="file_format"),
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="status"),
        CheckConstraint("date_from <= date_to", name="date_window"),
        CheckConstraint("row_count IS NULL OR row_count >= 0", name="row_count"),
        CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="idempotency_key",
        ),
        Index("ix_report_export_tenant_id_status", "tenant_id", "status"),
        Index("ix_report_export_tenant_id_created_at", "tenant_id", "created_at"),
        Index(
            "uq_report_export_tenant_idempotency",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_report_export_tenant_active",
            "tenant_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
    )


__all__ = ["ReportExport"]
