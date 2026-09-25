"""各平台可授权站点。新增站点只改这里，业务层不出现平台分支。"""

from __future__ import annotations

PLATFORM_SITES: dict[str, tuple[str, ...]] = {
    "amazon": ("US", "CA", "MX", "UK", "DE", "FR", "IT", "ES", "JP", "AU", "SG"),
    "shopee": ("SG", "MY", "TH", "ID", "VN", "PH", "TW", "BR"),
    "lazada": ("SG", "MY", "TH", "ID", "VN", "PH"),
    "tiktok": ("US", "GB", "ID", "MY", "TH", "VN", "PH", "SG"),
}

AMAZON_REGION: dict[str, str] = {
    "US": "NA",
    "CA": "NA",
    "MX": "NA",
    "UK": "EU",
    "DE": "EU",
    "FR": "EU",
    "IT": "EU",
    "ES": "EU",
    "JP": "FE",
    "AU": "FE",
    "SG": "FE",
}

AMAZON_CONSENT_HOST: dict[str, str] = {
    "NA": "https://sellercentral.amazon.com",
    "EU": "https://sellercentral-europe.amazon.com",
    "FE": "https://sellercentral.amazon.co.jp",
}

LAZADA_AUTH_HOST: dict[str, str] = {
    "SG": "https://auth.lazada.sg",
    "MY": "https://auth.lazada.com.my",
    "TH": "https://auth.lazada.co.th",
    "ID": "https://auth.lazada.co.id",
    "VN": "https://auth.lazada.vn",
    "PH": "https://auth.lazada.com.ph",
}


def supported_sites(platform: str) -> tuple[str, ...]:
    return PLATFORM_SITES.get(platform.lower(), ())


def site_supported(platform: str, site_code: str) -> bool:
    return site_code.upper() in supported_sites(platform)


__all__ = [
    "AMAZON_CONSENT_HOST",
    "AMAZON_REGION",
    "LAZADA_AUTH_HOST",
    "PLATFORM_SITES",
    "site_supported",
    "supported_sites",
]
