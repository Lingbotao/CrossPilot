"""报表导出与 SKU 日利润读取。只有这一层写 SQL。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.engines.report import EXPORT_PENDING, EXPORT_RUNNING
from app.models.finance import SkuProfitDaily
from app.models.report import ReportExport
from app.repositories.base import BaseRepository


class ReportExportRepository(BaseRepository[ReportExport]):
    model = ReportExport

    async def get_by_key(self, key: str) -> ReportExport | None:
        stmt = self.base_select().where(ReportExport.idempotency_key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def has_active(self) -> bool:
        stmt = select(ReportExport.id).where(ReportExport.status.in_((EXPORT_PENDING, EXPORT_RUNNING))).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def insert(self, row: ReportExport) -> str | None:
        """写入成功返回 None。唯一约束冲突返回 idempotency 或 active。"""

        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError as exc:
            self.session.expunge(row)
            return _integrity_kind(exc)
        return None


class SkuProfitExportRepository(BaseRepository[SkuProfitDaily]):
    model = SkuProfitDaily

    async def count_between(self, date_from: date, date_to: date) -> int:
        stmt = (
            select(func.count())
            .select_from(SkuProfitDaily)
            .where(SkuProfitDaily.stat_date >= date_from, SkuProfitDaily.stat_date <= date_to)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def page_between(
        self,
        date_from: date,
        date_to: date,
        *,
        after: tuple[date, int] | None,
        limit: int,
    ) -> list[SkuProfitDaily]:
        stmt = select(SkuProfitDaily).where(
            SkuProfitDaily.stat_date >= date_from,
            SkuProfitDaily.stat_date <= date_to,
        )
        if after is not None:
            after_date, after_id = after
            stmt = stmt.where(
                or_(
                    SkuProfitDaily.stat_date > after_date,
                    and_(SkuProfitDaily.stat_date == after_date, SkuProfitDaily.id > after_id),
                )
            )
        stmt = stmt.order_by(SkuProfitDaily.stat_date, SkuProfitDaily.id).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())


def _integrity_kind(exc: IntegrityError) -> str:
    detail = str(getattr(exc, "orig", exc))
    if "uq_report_export_tenant_idempotency" in detail:
        return "idempotency"
    if "uq_report_export_tenant_active" in detail:
        return "active"
    raise exc


__all__ = ["ReportExportRepository", "SkuProfitExportRepository"]
