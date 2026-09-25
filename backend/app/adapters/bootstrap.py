"""把四个平台适配器登记进全局注册表。重复调用无副作用。"""

from __future__ import annotations

from app.adapters.amazon.adapter import AmazonAdapter
from app.adapters.lazada.adapter import LazadaAdapter
from app.adapters.registry import adapter_registry
from app.adapters.shopee.adapter import ShopeeAdapter
from app.adapters.tiktok.adapter import TikTokAdapter

_DONE = False


def register_builtin_adapters() -> None:
    global _DONE
    if _DONE and adapter_registry.supports("shopee"):
        return
    adapter_registry.register("amazon", AmazonAdapter)
    adapter_registry.register("shopee", ShopeeAdapter)
    adapter_registry.register("lazada", LazadaAdapter)
    adapter_registry.register("tiktok", TikTokAdapter)
    _DONE = True


__all__ = ["register_builtin_adapters"]
