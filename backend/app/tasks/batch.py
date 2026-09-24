"""批量操作任务（M2/M3 落地）：批量发货、批量改价、批量刊登、库存回传。

约定：
- 批量任务是**长任务**，必须支持「部分成功」；
  返回值要带 ``succeeded / failed / errors[]``，前端据此展示可下载的失败清单
  （错误码 ``50003 BATCH_SHIP_PARTIAL_FAILED``）；
- 每个子项独立事务 —— 一个 SKU 失败不能把整批回滚掉；
- 进度写 Redis，前端轮询「异步任务中心」查看（M6-01 的 F12-06）。
"""

from __future__ import annotations

from typing import Any

from app.tasks.celery_app import celery_app

__all__ = ["celery_app"]


@celery_app.task(name="batch.placeholder")
def placeholder() -> dict[str, Any]:
    """占位：M2 起替换为真实的批量任务。"""
    return {"status": "not_implemented", "until": "M2"}
