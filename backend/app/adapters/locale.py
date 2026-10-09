"""店铺站点对应的内容语言。新增站点只改这里。"""

from __future__ import annotations

SITE_CONTENT_LANGUAGE: dict[str, str] = {
    "US": "en",
    "CA": "en",
    "MX": "es",
    "UK": "en",
    "GB": "en",
    "DE": "de",
    "FR": "fr",
    "IT": "it",
    "ES": "es",
    "JP": "ja",
    "AU": "en",
    "SG": "en",
    "MY": "ms",
    "TH": "th",
    "ID": "id",
    "VN": "vi",
    "PH": "en",
    "TW": "zh-TW",
    "BR": "pt",
}


def content_language(site_code: str) -> str | None:
    """站点没有配置语言时返回 None，调用方拒绝发布，不猜一种语言。"""

    return SITE_CONTENT_LANGUAGE.get(site_code.upper())


__all__ = ["SITE_CONTENT_LANGUAGE", "content_language"]
