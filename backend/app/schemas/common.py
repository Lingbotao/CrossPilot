"""Pydantic v2 请求/响应模型（契约定稿处）。

契约纪律（PRD 10.1）：
- 金额一律**字符串**传输 Decimal（``"12.340000"``），避免 JS ``number`` 精度丢失；
- 时间统一 ISO 8601 带时区；
- URL 用 kebab-case，JSON 字段用 snake_case；
- 每个响应带 ``trace_id``（由统一信封自动注入，业务模型不用管）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

# 金额字段类型别名：出参用，序列化为定长字符串
MoneyStr = Annotated[str, Field(description='十进制金额字符串，如 "12.340000"')]


class ORMModel(BaseModel):
    """可从 ORM 对象直接构造的基类。"""

    model_config = ConfigDict(from_attributes=True)


def money_to_str(value: Decimal | None, places: int = 6) -> str | None:
    """Decimal → 定长字符串。**全链路禁止 float**（PRD 约束 C4）。"""
    if value is None:
        return None
    return f"{Decimal(value):.{places}f}"


class MoneyMixin(BaseModel):
    """金额序列化 mixin：任何 ``Decimal`` 字段出参时都变成字符串。"""

    @field_serializer("*", when_used="json", check_fields=False)
    def _serialize_decimals(self, value: object) -> object:  # pragma: no cover - 由 pydantic 调用
        if isinstance(value, Decimal):
            return money_to_str(value)
        return value


class IdResponse(BaseModel):
    id: int = Field(description="Snowflake BIGINT ID（字符串化输出，避免 JS 大整数精度丢失）")

    @field_validator("id", mode="before")
    @classmethod
    def _to_int(cls, v: object) -> object:
        return int(v) if isinstance(v, str) else v

    @field_serializer("id")
    def _serialize_id(self, value: int) -> str:
        """BIGINT 超出 JS ``Number.MAX_SAFE_INTEGER``，必须按字符串出参。"""
        return str(value)


class TimestampedModel(BaseModel):
    created_at: datetime | None = None
    updated_at: datetime | None = None
