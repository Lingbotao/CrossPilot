"""订单状态映射与状态日志的请求/响应。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

StatusSourceCode = Literal["SYSTEM", "WEBHOOK", "MANUAL"]


class UpsertStatusMappingRequest(BaseModel):
    platform_code: str = Field(min_length=1, max_length=32)
    platform_status: str = Field(min_length=1, max_length=64)
    unified_status: str = Field(min_length=1, max_length=32)

    @field_validator("platform_code")
    @classmethod
    def _normalize_platform(cls, value: str) -> str:
        text = value.strip().lower()
        if not text:
            raise ValueError("平台编码不能为空")
        return text

    @field_validator("platform_status", "unified_status")
    @classmethod
    def _strip(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("不能为空")
        return text


class StatusMappingResponse(BaseModel):
    id: int
    platform_code: str
    platform_status: str
    unified_status: str
    updated_at: datetime

    @field_serializer("id")
    def _serialize_id(self, value: int) -> str:
        return str(value)


class OrderStatusLogResponse(BaseModel):
    id: int
    order_id: int
    from_status: str | None
    to_status: str
    platform_status: str
    operator_id: int | None
    source: StatusSourceCode
    remark: str | None
    created_at: datetime

    @field_serializer("id", "order_id", "operator_id")
    def _serialize_id(self, value: int | None) -> str | None:
        if value is None:
            return None
        return str(value)


__all__ = [
    "OrderStatusLogResponse",
    "StatusMappingResponse",
    "StatusSourceCode",
    "UpsertStatusMappingRequest",
]
