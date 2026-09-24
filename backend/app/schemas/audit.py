"""审计日志查询契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_serializer


class AuditLogResponse(BaseModel):
    id: int
    created_at: datetime
    user_id: int | None
    action: str
    resource: str
    resource_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    ip: str | None
    request_id: str | None

    @field_serializer("id", "user_id")
    def _serialize_id(self, value: int | None) -> str | None:
        return str(value) if value is not None else None


__all__ = ["AuditLogResponse"]
