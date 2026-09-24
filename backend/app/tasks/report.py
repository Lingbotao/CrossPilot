"""报表与导出任务（M5 落地）。

约定：
- 导出文件写对象存储（``S3_BUCKET_EXPORT``），返回**限时签名 URL**，
  不给静态公网地址（导出内容含成本与利润，泄露即事故）；
- 5 万行导出需 ≤60s（PRD NFR），因此必须流式写入 + 游标分页，不能一次性 load 进内存；
- 导出动作必须落审计（``AuditAction.DATA_EXPORT``）—— 谁在什么时候导了哪些数据。
"""

from __future__ import annotations

from typing import Any

from app.tasks.celery_app import celery_app

__all__ = ["celery_app"]


@celery_app.task(name="report.placeholder")
def placeholder() -> dict[str, Any]:
    """占位：M5 起替换为真实的导出任务。"""
    return {"status": "not_implemented", "until": "M5"}
