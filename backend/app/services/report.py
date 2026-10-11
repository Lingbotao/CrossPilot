"""报表导出。服务不写 SQL，文件字节进对象存储，不进响应信封。"""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ParamInvalidError, ReportExportInvalidError
from app.core.logging import get_logger
from app.engines.report import (
    EXPORT_EXPIRED,
    EXPORT_FAILED,
    EXPORT_PENDING,
    EXPORT_RUNNING,
    EXPORT_SUCCEEDED,
    SkuProfitExport,
    content_type,
    export_columns,
    export_filename,
    next_status,
    object_key,
    open_export,
    row_cells,
    validate_request,
)
from app.models.enums import AuditAction
from app.models.finance import SkuProfitDaily
from app.models.report import ReportExport
from app.object_store import ObjectStore, export_object_store
from app.repositories.identity import AuditLogRepository
from app.repositories.report import ReportExportRepository, SkuProfitExportRepository
from app.schemas.report import ReportExportCreate, ReportExportView

log = get_logger(__name__)


class ReportService:
    def __init__(self, session: AsyncSession, store: ObjectStore | None = None) -> None:
        self.exports = ReportExportRepository(session)
        self.profits = SkuProfitExportRepository(session)
        self.audit = AuditLogRepository(session)
        self.store = store if store is not None else export_object_store()

    async def create(
        self,
        payload: ReportExportCreate,
        *,
        tenant_id: int,
        actor_id: int,
        include_cost: bool,
        idempotency_key: str | None,
    ) -> tuple[ReportExportView, bool]:
        key = _clean_key(idempotency_key)
        existing = await self.exports.get_by_key(key)
        if existing is not None:
            return _view(existing), False
        try:
            validate_request(
                dataset=payload.dataset.strip(),
                file_format=payload.file_format.strip(),
                date_from=payload.date_from,
                date_to=payload.date_to,
                max_span_days=settings.report_export_max_span_days,
            )
        except ValueError as exc:
            raise ReportExportInvalidError(str(exc)) from exc
        if await self.exports.has_active():
            raise ConflictError("已有导出任务在进行")
        row = ReportExport(
            tenant_id=tenant_id,
            dataset=payload.dataset.strip(),
            file_format=payload.file_format.strip(),
            status=EXPORT_PENDING,
            date_from=payload.date_from,
            date_to=payload.date_to,
            include_cost=include_cost,
            expires_at=datetime.now(UTC) + timedelta(hours=settings.report_export_ttl_hours),
            idempotency_key=key,
            created_by=actor_id,
            updated_by=actor_id,
        )
        kind = await self.exports.insert(row)
        if kind == "idempotency":
            raced = await self.exports.get_by_key(key)
            if raced is None:
                raise ConflictError("导出任务冲突")
            return _view(raced), False
        if kind == "active":
            raise ConflictError("已有导出任务在进行")
        await self.audit.append_action(
            tenant_id=tenant_id,
            action=AuditAction.DATA_EXPORT,
            resource="report_export",
            user_id=actor_id,
            resource_id=row.id,
            after={
                "dataset": row.dataset,
                "file_format": row.file_format,
                "date_from": row.date_from.isoformat(),
                "date_to": row.date_to.isoformat(),
                "include_cost": row.include_cost,
                "status": EXPORT_PENDING,
            },
        )
        return _view(row), True

    async def get(self, export_id: int) -> ReportExportView:
        row = await self.exports.get(export_id)
        if row is None:
            raise NotFoundError("导出任务不存在")
        return _view(row)

    async def file(self, export_id: int) -> tuple[str, str, bytes]:
        row = await self.exports.get(export_id)
        if row is None:
            raise NotFoundError("导出任务不存在")
        if row.status != EXPORT_SUCCEEDED or not row.object_key:
            raise ReportExportInvalidError("导出文件还不能下载")
        if row.expires_at <= datetime.now(UTC):
            raise ReportExportInvalidError("导出文件已过期")
        body = self.store.get(row.object_key)
        if body is None:
            raise ReportExportInvalidError("导出文件已过期")
        name = export_filename(row.date_from, row.date_to, row.file_format)
        return name, content_type(row.file_format), body

    async def execute(self, export_id: int) -> ReportExportView:
        row = await self.exports.get(export_id)
        if row is None:
            raise NotFoundError("导出任务不存在")
        if row.status in {EXPORT_SUCCEEDED, EXPORT_FAILED, EXPORT_EXPIRED}:
            return _view(row)
        if row.status == EXPORT_PENDING:
            row.status = next_status(row.status, EXPORT_RUNNING)
            row.updated_by = row.created_by
            await self.exports.session.flush()
        try:
            count, key = await self._write(row)
        except ReportExportInvalidError as exc:
            await self._fail(row, exc.message)
            return _view(row)
        except Exception:
            log.exception("report_export_failed", export_id=row.id)
            await self._fail(row, "导出失败")
            return _view(row)
        row.row_count = count
        row.object_key = key
        row.error = None
        row.status = next_status(EXPORT_RUNNING, EXPORT_SUCCEEDED)
        row.updated_by = row.created_by
        await self.audit.append_action(
            tenant_id=int(row.tenant_id),
            action=AuditAction.DATA_EXPORT,
            resource="report_export",
            user_id=row.created_by,
            resource_id=row.id,
            after={
                "status": EXPORT_SUCCEEDED,
                "row_count": count,
                "include_cost": row.include_cost,
                "object_key": key,
            },
        )
        return _view(row)

    async def expire(self, export_id: int) -> None:
        row = await self.exports.get(export_id)
        if row is None or row.status == EXPORT_EXPIRED:
            return
        if row.object_key:
            self.store.delete(row.object_key)
            row.object_key = None
        row.status = next_status(row.status, EXPORT_EXPIRED)

    async def _write(self, row: ReportExport) -> tuple[int, str]:
        total = await self.profits.count_between(row.date_from, row.date_to)
        if total > settings.report_export_max_rows:
            raise ReportExportInvalidError("导出行数超过上限，请收窄日期")
        columns = export_columns(row.include_cost)
        headers = [label for _key, label in columns]
        key = object_key(int(row.tenant_id), int(row.id), row.file_format)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"export.{row.file_format}"
            with open_export(path, row.file_format, headers) as book:
                after: tuple[date, int] | None = None
                page_size = settings.report_export_page_size
                while True:
                    page = await self.profits.page_between(
                        row.date_from,
                        row.date_to,
                        after=after,
                        limit=page_size,
                    )
                    if not page:
                        break
                    for item in page:
                        book.write(row_cells(_profit(item), columns))
                        if book.count > settings.report_export_max_rows:
                            raise ReportExportInvalidError("导出行数超过上限，请收窄日期")
                    if len(page) < page_size:
                        break
                    last = page[-1]
                    after = (last.stat_date, int(last.id))
                count = book.count
            self.store.put(key, path.read_bytes(), content_type(row.file_format))
        return count, key

    async def _fail(self, row: ReportExport, message: str) -> None:
        row.status = next_status(EXPORT_RUNNING, EXPORT_FAILED)
        row.error = message[:512]
        row.object_key = None
        row.updated_by = row.created_by
        await self.audit.append_action(
            tenant_id=int(row.tenant_id),
            action=AuditAction.DATA_EXPORT,
            resource="report_export",
            user_id=row.created_by,
            resource_id=row.id,
            after={"status": EXPORT_FAILED, "error": row.error, "include_cost": row.include_cost},
        )


def _clean_key(value: str | None) -> str:
    if value is None or not value.strip():
        raise ParamInvalidError("缺少 Idempotency-Key")
    key = value.strip()
    if len(key) > 128:
        raise ParamInvalidError("Idempotency-Key 过长")
    return key


def _profit(row: SkuProfitDaily) -> SkuProfitExport:
    return SkuProfitExport(
        stat_date=row.stat_date,
        sku_id=int(row.sku_id),
        shop_id=int(row.shop_id),
        currency=str(row.currency).strip(),
        book_currency=str(row.book_currency).strip(),
        quantity=int(row.quantity),
        revenue=row.revenue,
        book_revenue=row.book_revenue,
        complete=bool(row.complete),
        cost_total=row.cost_total,
        net_profit=row.net_profit,
        net_margin=row.net_margin,
        book_cost_total=row.book_cost_total,
        book_net_profit=row.book_net_profit,
        fx_gain=row.fx_gain,
    )


def _view(row: ReportExport) -> ReportExportView:
    return ReportExportView(
        id=str(row.id),
        dataset=row.dataset,
        file_format=row.file_format,
        status=row.status,
        date_from=row.date_from,
        date_to=row.date_to,
        include_cost=row.include_cost,
        row_count=row.row_count,
        error=row.error,
        expires_at=row.expires_at,
    )


__all__ = ["ReportService"]
