"""★ 平台适配器层（约束 C2：新增平台时业务层零改动）。

## 这一层为什么必须存在

四个平台（Amazon / Shopee / Lazada / TikTok）的差异是**全维度的**：

| 差异点 | 例子 |
|---|---|
| 认证 | Amazon 用 LWA + SigV4；Shopee 用 partner_id + HMAC-SHA256；Lazada 用 app_key + MD5 签名 |
| 时间戳 | Shopee 时间戳偏差 >15min 直接认证失败 |
| 限流 | 各平台配额不同，且会动态变化 → 必须配置化（约束 C5） |
| 状态枚举 | 各平台订单状态名完全不同，映射关系落表（不硬编码） |
| 分页 | 有的用 offset，有的用 cursor，有的只能按时间窗拉 |
| 错误码 | 429 / 5xx / 业务错误 → **是否可重试**的判定规则各不相同 |

如果这些差异渗透进业务代码，新增一个平台就要改订单、库存、商品、发货……
所以：**所有平台差异只允许出现在 ``adapters/`` 目录内**。

## 落地节奏

- **M0（本阶段）**：目录与接口骨架定稿 —— 即本文件与 ``errors.py`` / ``registry.py``。
  目的是**先冻结抽象**，避免 M1 写第一个平台时才发现接口不对、回头改业务层。
- **M1**：``base.py``、四平台认证、fixture 报文与契约测试。真实 Sandbox 只切 ``platform_transport=live``。
- **M2**：``ratelimit.py`` 令牌桶与自适应降速；重试、熔断和死信在 ``sync_engine/``。订单入库从 M2-05 开始。

## ⚠️ 已知缺口（开发规划风险 N5，需在 M4 前定稿）

PRD 8.4 给出的 ``PlatformAdapter`` 骨架**缺少 F11 客服模块所需的三个方法**：

    fetch_messages / reply_message / fetch_reviews

本骨架按推荐方案处理：**V1 只做只读提醒（``fetch_messages``）+ 跳转平台后台回复**
（``reply_message`` 留到 V1.5）。这样 M5 不增量、约束 C2 不破；
若坚持 V1 站内回复，需重新评估 M5 人日。
"""

from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError, RetryDecision, classify_platform_error
from app.adapters.registry import (
    SUPPORTED_PLATFORMS,
    PlatformAdapterRegistry,
    adapter_registry,
)

__all__ = [
    "SUPPORTED_PLATFORMS",
    "AdapterError",
    "PlatformAdapterRegistry",
    "RetryDecision",
    "adapter_registry",
    "classify_platform_error",
    "register_builtin_adapters",
]

register_builtin_adapters()
