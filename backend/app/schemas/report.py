"""报表导出接口模型。主键是字符串，金额不在任务视图里出现。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class ReportExportCreate(BaseModel):
    dataset: str = "sku_profit"
    file_format: str = "csv"
    date_from: date
    date_to: date


class ReportExportView(BaseModel):
    id: str
    dataset: str
    file_format: str
    status: str
    date_from: date
    date_to: date
    include_cost: bool
    row_count: int | None
    error: str | None
    expires_at: datetime


__all__ = ["ReportExportCreate", "ReportExportView"]
