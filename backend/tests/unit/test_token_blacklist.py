"""jti 黑名单与 TTL（PRD A-02：登出失效 / Refresh 轮换）。"""

from __future__ import annotations

import pytest

from app.core.errors import UnauthenticatedError
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.core.token_blacklist import (
    InMemoryTokenBlacklist,
    assert_token_not_revoked,
    remaining_ttl_seconds,
    reset_token_blacklist,
    revoke_payload,
)


class TestRemainingTtl:
    def test_uses_exp_minus_now(self) -> None:
        payload = decode_token(create_access_token(1, 2))
        ttl = remaining_ttl_seconds(payload, now=payload.exp - 40)
        assert ttl == 40

    def test_floor_is_one_second(self) -> None:
        payload = decode_token(create_access_token(1, 2))
        assert remaining_ttl_seconds(payload, now=payload.exp + 10) == 1


class TestInMemoryBlacklist:
    @pytest.mark.asyncio
    async def test_revoke_then_hit(self) -> None:
        store = InMemoryTokenBlacklist()
        await store.revoke("abc", ttl_seconds=60)
        assert await store.is_revoked("abc")
        assert not await store.is_revoked("other")

    @pytest.mark.asyncio
    async def test_expired_entry_is_not_revoked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = InMemoryTokenBlacklist()
        clock = {"now": 1_000.0}

        monkeypatch.setattr("app.core.token_blacklist.time.time", lambda: clock["now"])
        await store.revoke("abc", ttl_seconds=10)
        assert await store.is_revoked("abc")

        clock["now"] = 1_011.0
        assert not await store.is_revoked("abc")

    @pytest.mark.asyncio
    async def test_empty_jti_is_ignored(self) -> None:
        store = InMemoryTokenBlacklist()
        await store.revoke("", ttl_seconds=60)
        assert store._until == {}


class TestRevokePayload:
    @pytest.mark.asyncio
    async def test_revoked_access_is_rejected(self) -> None:
        reset_token_blacklist()
        payload = decode_token(create_access_token(11, 22, "OWNER"))
        await assert_token_not_revoked(payload)
        await revoke_payload(payload)
        with pytest.raises(UnauthenticatedError, match="令牌已失效"):
            await assert_token_not_revoked(payload)

    @pytest.mark.asyncio
    async def test_revoked_refresh_is_rejected(self) -> None:
        reset_token_blacklist()
        payload = decode_token(create_refresh_token(11, 22, "OWNER"), expected_type="refresh")
        await revoke_payload(payload)
        with pytest.raises(UnauthenticatedError, match="令牌已失效"):
            await assert_token_not_revoked(payload)
