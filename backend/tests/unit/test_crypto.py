"""平台凭证字段级加密（AES-256-GCM）。"""

from __future__ import annotations

import base64

import pytest

from app.core.config import settings
from app.core.crypto import (
    CredentialCipher,
    CredentialCipherNotConfiguredError,
    get_cipher,
    mask_secret,
    redact_mapping,
    reset_cipher_cache,
)

KEY = (b"unit-test-key-32-bytes-padding" + b"0" * 32)[:32]


class TestCipher:
    def test_roundtrip(self) -> None:
        cipher = CredentialCipher(KEY)
        secret = "test-only-shopee-token-abc123"  # 形态即说明：夹具值，不是真凭证
        encrypted = cipher.encrypt(secret)
        assert encrypted != secret
        assert cipher.decrypt(encrypted) == secret

    def test_ciphertext_has_version_prefix(self) -> None:
        """带版本号是为了将来换算法时能平滑迁移 —— 没有版本号就无法轮换密钥。"""
        encrypted = CredentialCipher(KEY).encrypt("x")
        assert encrypted.startswith("v1:")

    def test_same_plaintext_produces_different_ciphertext(self) -> None:
        """随机 nonce：相同明文不能产生相同密文，否则能看出"两个店铺用了同一个 token"。"""
        cipher = CredentialCipher(KEY)
        assert cipher.encrypt("same") != cipher.encrypt("same")

    def test_wrong_key_cannot_decrypt(self) -> None:
        other = (b"another-key-32-bytes-padding" + b"0" * 32)[:32]
        encrypted = CredentialCipher(KEY).encrypt("secret")
        with pytest.raises(Exception):  # noqa: B017 - cryptography 抛 InvalidTag
            CredentialCipher(other).decrypt(encrypted)

    def test_tampered_ciphertext_is_detected(self) -> None:
        """GCM 自带完整性校验：改一个字节就必须解不开（防密文被篡改）。"""
        cipher = CredentialCipher(KEY)
        encrypted = cipher.encrypt("secret")
        version, _, body = encrypted.partition(":")
        raw = bytearray(base64.urlsafe_b64decode(body))
        raw[-1] ^= 0x01
        tampered = f"{version}:{base64.urlsafe_b64encode(bytes(raw)).decode()}"
        with pytest.raises(Exception):  # noqa: B017
            cipher.decrypt(tampered)

    def test_wrong_key_length_rejected(self) -> None:
        with pytest.raises(CredentialCipherNotConfiguredError):
            CredentialCipher(b"too-short")

    def test_unsupported_version_rejected(self) -> None:
        with pytest.raises(ValueError):
            CredentialCipher(KEY).decrypt("v9:abcdef")

    def test_empty_input_rejected(self) -> None:
        with pytest.raises(ValueError):
            CredentialCipher(KEY).decrypt("")

    def test_unicode_payload(self) -> None:
        cipher = CredentialCipher(KEY)
        assert cipher.decrypt(cipher.encrypt("店铺凭证-测试")) == "店铺凭证-测试"

    def test_optional_helpers(self) -> None:
        cipher = CredentialCipher(KEY)
        assert cipher.encrypt_optional(None) is None
        assert cipher.decrypt_optional(None) is None
        assert cipher.decrypt_optional(cipher.encrypt_optional("t")) == "t"


class TestCipherSingleton:
    def test_singleton_reads_settings_key(self) -> None:
        reset_cipher_cache()
        cipher = get_cipher()
        assert cipher.decrypt(cipher.encrypt("hello")) == "hello"

    def test_missing_key_raises_instead_of_silent_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """密钥缺失时必须抛错 —— 明文存密钥本身就是漏洞，不能静默降级。"""
        reset_cipher_cache()
        monkeypatch.setattr(settings, "credential_aes_key", "")
        with pytest.raises(CredentialCipherNotConfiguredError):
            get_cipher()
        reset_cipher_cache()


class TestRedaction:
    def test_mask_secret_hides_middle(self) -> None:
        masked = mask_secret("sk_live_abcdefghijklmn")
        assert masked.startswith("sk_l")
        assert "****" in masked
        assert "abcdefghij" not in masked

    def test_mask_short_value_fully(self) -> None:
        assert mask_secret("abc") == "***"

    def test_mask_empty(self) -> None:
        assert mask_secret("") == ""

    def test_redact_mapping_recurses(self) -> None:
        payload = {
            "access_token": "real-token-value",
            "nested": {"refresh_token": "another-real-value", "safe": 1},
        }
        redacted = redact_mapping(payload, {"access_token", "refresh_token"})
        assert redacted["access_token"] != "real-token-value"
        assert redacted["nested"]["refresh_token"] != "another-real-value"  # type: ignore[index]
        assert redacted["nested"]["safe"] == 1  # type: ignore[index]
