"""同步引擎基础设施错误。

连不上 Redis、或同一键的乐观写重试耗尽时抛出。
调用方应退避，不能因此放行把请求直接打到平台上。
"""

from __future__ import annotations


class StoreUnavailable(Exception):
    """限流、熔断、死信或分布式锁的存储暂时不可用。"""


__all__ = ["StoreUnavailable"]
