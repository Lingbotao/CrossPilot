"""各平台签名。只服务适配器，业务层不调用。"""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import quote


def shopee_sign(*, partner_id: str, partner_key: str, path: str, timestamp: int) -> str:
    """HMAC-SHA256(partner_key, partner_id + path + timestamp)。"""
    base = f"{partner_id}{path}{timestamp}"
    return hmac.new(partner_key.encode(), base.encode(), hashlib.sha256).hexdigest()


def lazada_sign(*, app_secret: str, path: str, params: dict[str, str]) -> str:
    """HMAC-SHA256(secret, path + 按 key 排序拼接的参数)。"""
    pieces = "".join(f"{key}{params[key]}" for key in sorted(params))
    base = f"{path}{pieces}"
    return hmac.new(app_secret.encode(), base.encode(), hashlib.sha256).hexdigest().upper()


def tiktok_sign(*, app_secret: str, path: str, params: dict[str, str]) -> str:
    """HMAC-SHA256(secret, secret + path + 排序参数 + secret)。"""
    pieces = "".join(f"{key}{params[key]}" for key in sorted(params) if key != "sign")
    base = f"{app_secret}{path}{pieces}{app_secret}"
    return hmac.new(app_secret.encode(), base.encode(), hashlib.sha256).hexdigest()


def shopee_push_sign(*, partner_key: str, url: str, body: bytes) -> str:
    """Shopee Push：HMAC-SHA256(partner_key, callback_url + '|' + 原始报文)。"""
    base = url.encode() + b"|" + body
    return hmac.new(partner_key.encode(), base, hashlib.sha256).hexdigest()


def lazada_push_sign(*, app_secret: str, body: bytes) -> str:
    """Lazada Push：HMAC-SHA256(app_secret, 原始报文)。"""
    return hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()


def tiktok_push_sign(*, app_secret: str, app_key: str, body: bytes) -> str:
    """TikTok Shop Webhook：HMAC-SHA256(app_secret, app_key + 原始报文)。"""
    return hmac.new(app_secret.encode(), app_key.encode() + body, hashlib.sha256).hexdigest()


def signatures_match(expected: str, provided: str) -> bool:
    left = expected.strip().lower().encode()
    right = provided.strip().lower().encode()
    if not left or len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def amazon_consent_url(*, host: str, application_id: str, redirect_uri: str, state: str) -> str:
    return (
        f"{host}/apps/authorize/consent"
        f"?application_id={quote(application_id, safe='')}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&state={quote(state, safe='')}"
        "&version=beta"
    )


__all__ = [
    "amazon_consent_url",
    "lazada_push_sign",
    "lazada_sign",
    "shopee_push_sign",
    "shopee_sign",
    "signatures_match",
    "tiktok_push_sign",
    "tiktok_sign",
]
