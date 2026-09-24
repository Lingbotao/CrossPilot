"""平台凭证字段级加密（AES-256-GCM）。

为什么必须字段级加密而不是"整库加密"：
    平台 access_token / refresh_token 泄露 = 攻击者可直接操作用户店铺。
    整库加密挡不住拖库后的应用内横向查询，字段级加密让密文即使落库也无用。

密文格式：``v1:<base64url(nonce(12) || ciphertext || tag(16))>``
    带版本号是为了**将来换算法时能平滑迁移** —— 没有版本号的加密方案无法轮换密钥。
"""

from __future__ import annotations

import base64
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.core.errors import AppError, ErrorCode

_VERSION = "v1"
_NONCE_BYTES = 12
_KEY_BYTES = 32  # AES-256


class CredentialCipherNotConfiguredError(AppError):
    code = ErrorCode.INTERNAL_ERROR
    message = "凭证加密密钥未配置（CREDENTIAL_AES_KEY）"


class CredentialCipher:
    """AES-256-GCM 加解密器。非线程安全问题不存在 —— 每次调用独立 nonce。"""

    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_BYTES:
            raise CredentialCipherNotConfiguredError(
                f"CREDENTIAL_AES_KEY 必须为 {_KEY_BYTES} 字节，当前 {len(key)} 字节"
            )
        self._aesgcm = AESGCM(key)

    def encrypt(self, plaintext: str) -> str:
        if plaintext is None:
            raise ValueError("待加密内容不能为 None")
        nonce = os.urandom(_NONCE_BYTES)
        blob = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return f"{_VERSION}:{base64.urlsafe_b64encode(nonce + blob).decode('ascii')}"

    def decrypt(self, token: str) -> str:
        if not token:
            raise ValueError("待解密内容不能为空")
        version, _, payload = token.partition(":")
        if version != _VERSION or not payload:
            raise ValueError(f"密文格式不支持：{version or '空'}")
        raw = base64.urlsafe_b64decode(payload)
        nonce, blob = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
        return self._aesgcm.decrypt(nonce, blob, None).decode("utf-8")

    # ---- 便捷方法：JSONB 里的敏感子字段 ----
    def encrypt_optional(self, plaintext: str | None) -> str | None:
        return None if plaintext is None else self.encrypt(plaintext)

    def decrypt_optional(self, token: str | None) -> str | None:
        return None if token is None else self.decrypt(token)


_cipher: CredentialCipher | None = None


def get_cipher() -> CredentialCipher:
    """进程内单例。密钥缺失时抛错而不是静默降级 —— 明文存密钥本身就是漏洞。"""
    global _cipher
    if _cipher is None:
        key = settings.credential_aes_key_bytes
        if not key:
            raise CredentialCipherNotConfiguredError()
        _cipher = CredentialCipher(key)
    return _cipher


def reset_cipher_cache() -> None:
    """仅供测试使用。"""
    global _cipher
    _cipher = None


def mask_secret(value: str, keep: int = 4) -> str:
    """日志/接口返回时的脱敏：``sk_live_abcd****``。禁止把完整密钥写进任何日志。"""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 4}{value[-keep:]}"


def redact_mapping(data: dict[str, Any], sensitive_keys: set[str]) -> dict[str, Any]:
    """按 key 名递归脱敏字典，用于把平台原始报文写日志前清洗。"""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in sensitive_keys and isinstance(value, str):
            out[key] = mask_secret(value)
        elif isinstance(value, dict):
            out[key] = redact_mapping(value, sensitive_keys)
        else:
            out[key] = value
    return out
