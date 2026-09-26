"""M2 同步引擎地基：限流、分级队列、幂等、重试与熔断。

订单入库从 M2-05 开始调用这里的幂等键、令牌桶和熔断器。
平台差异仍只留在 ``adapters/``，本包不写 ``if platform ==``。
"""

from app.sync_engine.idempotency import (
    DedupHint,
    WriteAction,
    decide_write,
    order_idempotency_key,
)
from app.sync_engine.policy import SyncPolicy
from app.sync_engine.resilience import RetryAction, plan_retry

__all__ = [
    "DedupHint",
    "RetryAction",
    "SyncPolicy",
    "WriteAction",
    "decide_write",
    "order_idempotency_key",
    "plan_retry",
]
