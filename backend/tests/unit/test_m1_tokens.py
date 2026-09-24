"""M1 单用途令牌与二次确认。"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.core.errors import AppError, ErrorCode, UnauthenticatedError
from app.core.security import (
    consume_confirmation_token,
    create_purpose_token,
    decode_purpose_token,
)


def test_purpose_token_rejects_wrong_purpose() -> None:
    token = create_purpose_token(user_id=1, tenant_id=2, purpose="email_verify", ttl=timedelta(minutes=5))
    with pytest.raises(UnauthenticatedError):
        decode_purpose_token(token, "member_invite")


def test_confirmation_token_is_bound_and_single_use() -> None:
    token = create_purpose_token(
        user_id=11,
        tenant_id=22,
        purpose="confirmation",
        action="member.remove",
        ttl=timedelta(minutes=5),
    )
    asyncio.run(consume_confirmation_token(token, action="member.remove", user_id=11, tenant_id=22))
    with pytest.raises(AppError) as captured:
        asyncio.run(consume_confirmation_token(token, action="member.remove", user_id=11, tenant_id=22))
    assert captured.value.code == ErrorCode.CONFIRMATION_INVALID


def test_confirmation_token_is_bound_to_action() -> None:
    token = create_purpose_token(
        user_id=11,
        tenant_id=22,
        purpose="confirmation",
        action="member.remove",
        ttl=timedelta(minutes=5),
    )
    with pytest.raises(AppError) as captured:
        asyncio.run(consume_confirmation_token(token, action="shop.revoke", user_id=11, tenant_id=22))
    assert captured.value.code == ErrorCode.CONFIRMATION_INVALID
