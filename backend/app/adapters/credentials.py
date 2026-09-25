"""读取平台应用级 App Key。店铺级 token 不在这里。"""

from __future__ import annotations

from app.adapters.transport import use_fixture_transport
from app.core.config import settings
from app.core.errors import AppError, ErrorCode


def app_credentials(platform: str) -> tuple[str, str]:
    mapping = {
        "amazon": (settings.platform_amazon_app_key, settings.platform_amazon_app_secret),
        "shopee": (settings.platform_shopee_app_key, settings.platform_shopee_app_secret),
        "lazada": (settings.platform_lazada_app_key, settings.platform_lazada_app_secret),
        "tiktok": (settings.platform_tiktok_app_key, settings.platform_tiktok_app_secret),
    }
    key, secret = mapping.get(platform.lower(), ("", ""))
    if key and secret:
        return key, secret
    if use_fixture_transport():
        return "fixture-app", "fixture-secret"
    raise AppError("平台应用凭证未配置", code=ErrorCode.GRANT_FAILED)


__all__ = ["app_credentials"]
