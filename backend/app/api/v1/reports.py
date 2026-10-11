"""报表导出（F10-10）。创建任务需要二次确认，下载不发公网地址。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import Response

from app.core.deps import DbSession, Identity, require_confirmation, require_permission
from app.core.errors import ParamInvalidError
from app.core.logging import get_logger
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.listing import parse_id
from app.schemas.report import ReportExportCreate, ReportExportView
from app.services.report import ReportService
from app.tasks.report import schedule_export

router = APIRouter(prefix="/reports", tags=["报表"])
log = get_logger(__name__)

Reader = Annotated[Identity, Depends(require_permission(Perm.REPORT_EXPORT))]


def _path_id(value: str) -> int:
    try:
        return parse_id(value)
    except ValueError as exc:
        raise ParamInvalidError("导出编号无效") from exc


@router.post("/export", response_model=ApiResponse[ReportExportView], summary="创建导出任务")
async def create_export(
    payload: ReportExportCreate,
    identity: Annotated[Identity, Depends(require_permission(Perm.REPORT_EXPORT))],
    _confirmed: Annotated[Identity, Depends(require_confirmation("report.export"))],
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ReportExportView]:
    view, created = await ReportService(session).create(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        include_cost=identity.can_view_cost,
        idempotency_key=idempotency_key,
    )
    if created:
        await session.commit()
        await schedule_export(int(identity.tenant.id), int(view.id))
    return ok(view)


@router.get("/exports/{export_id}", response_model=ApiResponse[ReportExportView], summary="查询导出任务")
async def get_export(
    export_id: str,
    identity: Reader,
    session: DbSession,
) -> ApiResponse[ReportExportView]:
    del identity
    view = await ReportService(session).get(_path_id(export_id))
    return ok(view)


@router.get("/exports/{export_id}/file", summary="下载导出文件")
async def download_export(
    export_id: str,
    identity: Reader,
    session: DbSession,
) -> Response:
    del identity
    filename, media_type, body = await ReportService(session).file(_path_id(export_id))
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
