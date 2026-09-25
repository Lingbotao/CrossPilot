"""平台限流配额配置（约束 C5）。

这些数字不是业务代码里的魔法数，而是一份可替换的配置。
各平台后台配额会变，Sandbox 凭证到位后以开发者后台实时值为准，改这里即可。
M2 起由 ``platform_rate_limit`` 表覆盖本默认值。
"""

from __future__ import annotations

from app.adapters.base import RateLimitSpec

# qps / burst / 维度 / 单次批量上限
_DEFAULTS: dict[str, tuple[int, int, str, int]] = {
    "amazon": (1, 5, "shop", 1),
    "shopee": (10, 20, "shop", 50),
    "lazada": (5, 10, "app", 50),
    "tiktok": (5, 10, "shop", 50),
}


def quota_for(platform: str) -> RateLimitSpec:
    qps, burst, dimension, batch_limit = _DEFAULTS[platform.lower()]
    return RateLimitSpec(qps=qps, burst=burst, dimension=dimension, batch_limit=batch_limit)


__all__ = ["quota_for"]
