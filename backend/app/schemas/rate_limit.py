"""平台限流配额的请求与响应。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

RateLimitSource = Literal["table", "default"]


class UpsertRateLimitRequest(BaseModel):
    platform_code: str = Field(min_length=1, max_length=32)
    dimension: str = Field(min_length=1, max_length=16)
    qps: int = Field(ge=1, le=1_000_000)
    burst: int = Field(ge=1, le=1_000_000)
    batch_limit: int = Field(ge=1, le=1_000_000)
    daily_quota: int | None = Field(default=None, ge=1, le=100_000_000)
    concurrency: int | None = Field(default=None, ge=1, le=10_000)

    @field_validator("platform_code", "dimension")
    @classmethod
    def _strip(cls, value: str) -> str:
        text = value.strip().lower()
        if not text:
            raise ValueError("不能为空")
        return text


class RateLimitResponse(BaseModel):
    id: int | None
    platform_code: str
    dimension: str
    qps: int
    burst: int
    batch_limit: int
    daily_quota: int | None
    concurrency: int | None
    source: RateLimitSource
    updated_at: datetime | None

    @field_serializer("id")
    def _id(self, value: int | None) -> str | None:
        if value is None:
            return None
        return str(value)


__all__ = ["RateLimitResponse", "RateLimitSource", "UpsertRateLimitRequest"]
