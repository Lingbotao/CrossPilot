"""批量操作任务。

M2 的批量发货走 ``POST /orders/batch-ship``（请求内逐单保存点，失败单可重试）。
异步任务中心的进度轮询仍留到 M6。批量改价、刊登、库存回传在后续里程碑。

约定：
- 必须支持部分成功。返回 ``succeeded / failed / results[]``，失败单可再次提交
  （错误码 ``50003 BATCH_SHIP_PARTIAL_FAILED``）；
- 每个订单一段保存点，一单失败不回滚同批已成功的单。
"""

from __future__ import annotations

from typing import Any

from app.tasks.celery_app import celery_app

__all__ = ["celery_app"]


@celery_app.task(name="batch.placeholder")
def placeholder() -> dict[str, Any]:
    """占位：M2 起替换为真实的批量任务。"""
    return {"status": "not_implemented", "until": "M2"}
