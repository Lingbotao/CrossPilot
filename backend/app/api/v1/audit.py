"""审计日志查询接口。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbSession, Identity, require_permission
from app.core.errors import ParamInvalidError
from app.core.pagination import PageData
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.models.enums import AuditAction
from app.schemas.audit import AuditLogResponse
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audit-logs", tags=["审计"])
AuditReader = Annotated[Identity, Depends(require_permission(Perm.AUDIT_READ))]


@router.get("", response_model=ApiResponse[PageData[AuditLogResponse]], summary="审计日志")
async def list_audit_logs(
    _identity: AuditReader,
    session: DbSession,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    action: str | None = None,
    user_id: int | None = None,
    resource: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> ApiResponse[PageData[AuditLogResponse]]:
    if action is not None and action not in AuditAction.ALL:
        raise ParamInvalidError("未知审计动作")
    if created_from and created_to and created_from > created_to:
        raise ParamInvalidError("开始时间不能晚于结束时间")
    result = await AuditService(session).list_logs(
        cursor=cursor,
        limit=limit,
        action=action,
        user_id=user_id,
        resource=resource,
        created_from=created_from,
        created_to=created_to,
    )
    return ok(result)


__all__ = ["router"]
