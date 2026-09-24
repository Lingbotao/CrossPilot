"""密码哈希与 JWT 双 Token 安全测试。"""

from __future__ import annotations

import time

import pytest

from app.core.config import settings
from app.core.errors import TokenExpiredError, UnauthenticatedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_token_pair,
    decode_token,
    hash_password,
    password_strength_error,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self) -> None:
        hashed = hash_password("Str0ng-Passw0rd!")
        assert hashed != "Str0ng-Passw0rd!"
        assert verify_password("Str0ng-Passw0rd!", hashed)

    def test_wrong_password_rejected(self) -> None:
        hashed = hash_password("Str0ng-Passw0rd!")
        assert not verify_password("Str0ng-Passw0rd", hashed)

    def test_same_password_produces_different_hashes(self) -> None:
        """加盐的意义：两个用户用同样的密码，库里也不能出现同样的哈希。"""
        assert hash_password("same-password-123") != hash_password("same-password-123")

    def test_long_password_is_not_silently_truncated(self) -> None:
        """bcrypt 只取前 72 字节 —— 预哈希后必须能区分 72 字节之后的内容。"""
        base = "x" * 100
        hashed = hash_password(base + "AAAA")
        assert verify_password(base + "AAAA", hashed)
        assert not verify_password(base + "BBBB", hashed)

    def test_empty_password_rejected(self) -> None:
        with pytest.raises(ValueError):
            hash_password("")

    def test_verify_handles_garbage_hash(self) -> None:
        assert not verify_password("whatever", "not-a-bcrypt-hash")
        assert not verify_password("", "")


class TestPasswordStrength:
    @pytest.mark.parametrize("weak", ["short1!A", "alllowercasebutlong", "1234567890", "NoSpecial1234".lower()])
    def test_weak_passwords_flagged(self, weak: str) -> None:
        assert password_strength_error(weak) is not None

    def test_strong_password_passes(self) -> None:
        assert password_strength_error("Str0ng-Passw0rd!") is None


class TestTokenPair:
    def test_pair_contains_both_tokens(self) -> None:
        pair = create_token_pair(1001, 2001, "OWNER")
        assert pair.access_token and pair.refresh_token
        assert pair.access_token != pair.refresh_token
        assert pair.expires_in == settings.jwt_access_ttl_minutes * 60

    def test_access_token_roundtrip(self) -> None:
        token = create_access_token(1001, 2001, "ADMIN")
        payload = decode_token(token, expected_type="access")
        assert payload.sub == 1001
        assert payload.tid == 2001
        assert payload.role == "ADMIN"
        assert payload.typ == "access"
        assert payload.exp > payload.iat

    def test_refresh_token_roundtrip(self) -> None:
        payload = decode_token(create_refresh_token(7, 8), expected_type="refresh")
        assert payload.sub == 7
        assert payload.typ == "refresh"

    def test_refresh_cannot_be_used_as_access(self) -> None:
        """★ 用 refresh token 冒充 access token 必须失败。"""
        refresh = create_refresh_token(1, 2)
        with pytest.raises(UnauthenticatedError):
            decode_token(refresh, expected_type="access")

    def test_access_cannot_be_used_as_refresh(self) -> None:
        access = create_access_token(1, 2)
        with pytest.raises(UnauthenticatedError):
            decode_token(access, expected_type="refresh")

    def test_tampered_token_rejected(self) -> None:
        token = create_access_token(1, 2)
        head, payload, _sig = token.split(".")
        with pytest.raises(UnauthenticatedError):
            decode_token(f"{head}.{payload}.AAAAinvalidsignature")

    def test_token_signed_with_other_secret_rejected(self) -> None:
        import jwt

        forged = jwt.encode(
            {"sub": "1", "tid": 2, "typ": "access", "jti": "x", "iat": 0, "exp": 9999999999},
            "another-secret",
            algorithm="HS256",
        )
        with pytest.raises(UnauthenticatedError):
            decode_token(forged)

    def test_expired_token_raises_token_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "jwt_access_ttl_minutes", -1)
        token = create_access_token(1, 2)
        with pytest.raises(TokenExpiredError):
            decode_token(token)

    def test_jti_is_unique_per_token(self) -> None:
        """jti 唯一是刷新轮换与登出黑名单的前提。"""
        a = decode_token(create_access_token(1, 2))
        b = decode_token(create_access_token(1, 2))
        assert a.jti != b.jti

    def test_platform_level_token_can_have_no_tenant(self) -> None:
        payload = decode_token(create_access_token(1))
        assert payload.tid is None

    def test_iat_is_recent(self) -> None:
        payload = decode_token(create_access_token(1, 2))
        assert abs(payload.iat - int(time.time())) < 5
