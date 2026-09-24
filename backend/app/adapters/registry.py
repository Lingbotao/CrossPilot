"""适配器注册表（工厂）。

业务层永远只写 ``adapter_registry.get("shopee")``，**不允许**出现
``if platform == "shopee": ... elif platform == "lazada": ...`` ——
那是"新增平台要改业务层"的起点，直接违反约束 C2。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from app.core.errors import PlatformUnsupportedError

if TYPE_CHECKING:  # pragma: no cover - 避免运行期循环导入
    from app.adapters.base import PlatformAdapter

SUPPORTED_PLATFORMS: Final[tuple[str, ...]] = ("amazon", "shopee", "lazada", "tiktok")

# 各平台账号唯一键的字段名不同（Amazon 是 sellerId，Shopee 是 shop_id…），
# 这里只登记"平台编码 → 显示名"，具体差异留给适配器实现。
PLATFORM_DISPLAY_NAMES: Final[dict[str, str]] = {
    "amazon": "Amazon",
    "shopee": "Shopee",
    "lazada": "Lazada",
    "tiktok": "TikTok Shop",
}


class PlatformAdapterRegistry:
    """惰性注册表：适配器按需构造，避免启动时把所有平台 SDK 都拉起来。"""

    def __init__(self) -> None:
        self._factories: dict[str, type[PlatformAdapter]] = {}
        self._instances: dict[str, PlatformAdapter] = {}

    def register(self, platform: str, adapter_cls: type[PlatformAdapter]) -> None:
        self._factories[platform.lower()] = adapter_cls

    def registered(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def get(self, platform: str) -> PlatformAdapter:
        key = platform.lower()
        if key in self._instances:
            return self._instances[key]
        factory = self._factories.get(key)
        if factory is None:
            raise PlatformUnsupportedError(
                f"平台 {platform} 尚未接入（已接入：{', '.join(self.registered()) or '无'}）"
            )
        instance = factory()
        self._instances[key] = instance
        return instance

    def supports(self, platform: str) -> bool:
        return platform.lower() in self._factories


adapter_registry = PlatformAdapterRegistry()

__all__ = [
    "PLATFORM_DISPLAY_NAMES",
    "SUPPORTED_PLATFORMS",
    "PlatformAdapterRegistry",
    "adapter_registry",
]
