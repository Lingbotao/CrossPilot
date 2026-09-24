"""统一响应封装（PRD 10.1）。

成功与失败共用同一信封：

    { "code": 0, "message": "success", "data": {...}, "trace_id": "...", "timestamp": "..." }

**所有**响应都经过这里，避免出现「有的接口裸返回 list、有的包了 data」这种契约漂移。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from app.core.context import get_trace_id

T = TypeVar("T")


def _now_utc() -> datetime:
    return datetime.now(UTC)


class ApiResponse(BaseModel, Generic[T]):
    """统一响应信封。``code == 0`` 表示成功。"""

    code: int = 0
    message: str = "success"
    data: T | None = None
    trace_id: str | None = Field(default=None, description="全链路追踪 ID（ULID）")
    timestamp: datetime = Field(default_factory=_now_utc)


class ErrorData(BaseModel):
    """错误响应的 data 段。业务方需要携带上下文（如 shop_id）时用这个。"""

    detail: dict[str, Any] = Field(default_factory=dict)


def ok(data: T | None = None, message: str = "success") -> ApiResponse[T]:
    """成功响应。"""
    return ApiResponse[T](code=0, message=message, data=data, trace_id=get_trace_id())


def fail(
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> ApiResponse[dict[str, Any]]:
    """失败响应。``code`` 取自 ``ErrorCode``。"""
    return ApiResponse[dict[str, Any]](
        code=code,
        message=message,
        data=data or {},
        trace_id=get_trace_id(),
    )


def is_success(payload: dict[str, Any]) -> bool:
    return int(payload.get("code", -1)) == 0
