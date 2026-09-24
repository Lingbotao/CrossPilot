"""密码哈希与 JWT 双 Token（PRD NFR-S 系列）。

设计要点：
- 密码用 **bcrypt**，但先做 SHA-256 预哈希 —— bcrypt 只取前 72 字节，超长密码会被静默截断。
  预哈希后固定 44 字节，既不截断也不会被"密码长度"探测。
- 双 Token：Access 15min（不落库，靠签名）、Refresh 7d（带 jti）。
  登出与 Refresh 轮换把 ``jti`` 写入 ``app.core.token_blacklist``（内存 + Redis）。
- Token 里带 ``tid``（tenant_id）—— 租户上下文从令牌恢复，不从请求参数取，
  否则攻击者改个 query 参数就能换租户。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.errors import TokenExpiredError, UnauthenticatedError

TokenType = Literal["access", "refresh"]
PurposeType = Literal["email_verify", "member_invite", "confirmation"]

_ALGO_BCRYPT_SHA256 = "bcrypt-sha256"


# ------------------------------------------------------------------ 密码
def _prehash(password: str) -> bytes:
    """SHA-256 → base64，固定 44 字节，规避 bcrypt 的 72 字节上限。"""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("密码不能为空")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(_prehash(password), salt).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """恒定时间比对；任何异常一律视为失败，不向调用方暴露细节。"""
    if not password or not hashed:
        return False
    try:
        return bool(hmac.compare_digest(bcrypt.hashpw(_prehash(password), hashed.encode()).decode(), hashed))
    except (ValueError, TypeError):
        return False


def password_strength_error(password: str) -> str | None:
    """返回不合规原因；合规返回 None（PRD F1-01 密码强度要求）。"""
    if len(password) < 10:
        return "密码至少 10 位"
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 3:
        return "密码需包含大写字母、小写字母、数字、符号中的至少三类"
    return None


# ------------------------------------------------------------------ JWT
class TokenPayload(BaseModel):
    sub: int = Field(description="用户 ID")
    tid: int | None = Field(default=None, description="租户 ID（平台级用户可为空）")
    role: str | None = Field(default=None, description="角色码")
    typ: TokenType = Field(description="access | refresh")
    jti: str = Field(description="令牌唯一 ID，用于刷新轮换与登出黑名单")
    iat: int
    exp: int


class PurposeTokenPayload(BaseModel):
    """短期单用途令牌；不能作为 API 鉴权令牌使用。"""

    sub: int
    tid: int | None = None
    purpose: PurposeType
    action: str | None = None
    email: str | None = None
    jti: str
    iat: int
    exp: int


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 0


def _encode(
    *,
    user_id: int,
    token_type: TokenType,
    tenant_id: int | None,
    role_code: str | None,
    ttl: timedelta,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),  # JWT 规范要求 sub 为字符串
        "tid": tenant_id,
        "role": role_code,
        "typ": token_type,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def create_access_token(user_id: int, tenant_id: int | None = None, role_code: str | None = None) -> str:
    return _encode(
        user_id=user_id,
        token_type="access",
        tenant_id=tenant_id,
        role_code=role_code,
        ttl=timedelta(minutes=settings.jwt_access_ttl_minutes),
    )


def create_refresh_token(user_id: int, tenant_id: int | None = None, role_code: str | None = None) -> str:
    return _encode(
        user_id=user_id,
        token_type="refresh",
        tenant_id=tenant_id,
        role_code=role_code,
        ttl=timedelta(days=settings.jwt_refresh_ttl_days),
    )


def create_token_pair(user_id: int, tenant_id: int | None = None, role_code: str | None = None) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(user_id, tenant_id, role_code),
        refresh_token=create_refresh_token(user_id, tenant_id, role_code),
        expires_in=settings.jwt_access_ttl_minutes * 60,
    )


def decode_token(token: str, expected_type: TokenType = "access") -> TokenPayload:
    """解析并校验令牌。类型不匹配也按未认证处理（防用 refresh 冒充 access）。"""
    try:
        raw = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.PyJWTError as exc:
        raise UnauthenticatedError("令牌无效") from exc

    if raw.get("typ") != expected_type:
        raise UnauthenticatedError("令牌类型不匹配")

    try:
        return TokenPayload(
            sub=int(raw["sub"]),
            tid=int(raw["tid"]) if raw.get("tid") is not None else None,
            role=raw.get("role"),
            typ=raw["typ"],
            jti=raw["jti"],
            iat=int(raw["iat"]),
            exp=int(raw["exp"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UnauthenticatedError("令牌载荷不完整") from exc


def create_purpose_token(
    *,
    user_id: int,
    purpose: PurposeType,
    ttl: timedelta,
    tenant_id: int | None = None,
    action: str | None = None,
    email: str | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tid": tenant_id,
        "purpose": purpose,
        "action": action,
        "email": email,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_purpose_token(token: str, expected_purpose: PurposeType) -> PurposeTokenPayload:
    try:
        raw = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.PyJWTError as exc:
        raise UnauthenticatedError("令牌无效") from exc
    if raw.get("purpose") != expected_purpose:
        raise UnauthenticatedError("令牌用途不匹配")
    try:
        return PurposeTokenPayload(
            sub=int(raw["sub"]),
            tid=int(raw["tid"]) if raw.get("tid") is not None else None,
            purpose=raw["purpose"],
            action=raw.get("action"),
            email=raw.get("email"),
            jti=str(raw["jti"]),
            iat=int(raw["iat"]),
            exp=int(raw["exp"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UnauthenticatedError("令牌载荷不完整") from exc


async def consume_confirmation_token(token: str, *, action: str, user_id: int, tenant_id: int) -> None:
    """校验并一次性消费敏感操作确认令牌。"""
    import time

    from app.core.errors import AppError, ErrorCode
    from app.core.token_blacklist import get_token_blacklist

    try:
        payload = decode_purpose_token(token, "confirmation")
    except (TokenExpiredError, UnauthenticatedError) as exc:
        raise AppError("确认令牌无效或已过期", code=ErrorCode.CONFIRMATION_INVALID) from exc
    if (
        payload.sub != user_id
        or payload.tid != tenant_id
        or payload.action != action
        or await get_token_blacklist().is_revoked(payload.jti)
    ):
        raise AppError("确认令牌无效或已使用", code=ErrorCode.CONFIRMATION_INVALID)
    ttl = max(1, payload.exp - int(time.time()))
    await get_token_blacklist().revoke(payload.jti, ttl)
