"""审计日志只读查询。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import PageData, build_cursor_page, decode_cursor
from app.repositories.identity import AuditLogRepository
from app.schemas.audit import AuditLogResponse


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.logs = AuditLogRepository(session)

    async def list_logs(
        self,
        *,
        cursor: str | None,
        limit: int,
        action: str | None,
        user_id: int | None,
        resource: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
    ) -> PageData[AuditLogResponse]:
        decoded = decode_cursor(cursor) if cursor else {}
        raw_id = decoded.get("id")
        before_id = int(raw_id) if raw_id and str(raw_id).isdigit() else None
        rows = await self.logs.list_cursor(
            limit=limit,
            before_id=before_id,
            action=action,
            user_id=user_id,
            resource=resource,
            created_from=created_from,
            created_to=created_to,
        )
        items = [
            AuditLogResponse(
                id=row.id,
                created_at=row.created_at,
                user_id=row.user_id,
                action=row.action,
                resource=row.resource,
                resource_id=row.resource_id,
                before=row.before,
                after=row.after,
                ip=row.ip,
                request_id=row.request_id,
            )
            for row in rows
        ]
        return build_cursor_page(items, limit)


__all__ = ["AuditService"]
