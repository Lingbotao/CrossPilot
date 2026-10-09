"""商品主数据查询。软删除行不参与编码唯一和列表。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError, ErrorCode
from app.models.product import ProductImage, Sku, Spu
from app.repositories.base import BaseRepository


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _is_sku_code_conflict(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
    return code == "23505" or "uq_sku_tenant_id_sku_code_active" in str(orig or exc)


class SpuRepository(BaseRepository[Spu]):
    model = Spu

    async def list_cursor(
        self,
        *,
        limit: int,
        before_id: int | None,
        status: str | None,
        title: str | None,
    ) -> list[Spu]:
        stmt = self.base_select()
        if before_id is not None:
            stmt = stmt.where(Spu.id < before_id)
        if status:
            stmt = stmt.where(Spu.status == status)
        if title:
            stmt = stmt.where(Spu.title.ilike(_like(title), escape="\\"))
        stmt = stmt.order_by(Spu.id.desc()).limit(limit + 1)
        return list((await self.session.execute(stmt)).scalars().all())


class SkuRepository(BaseRepository[Sku]):
    model = Sku

    async def add(self, obj: Sku) -> Sku:
        try:
            return await super().add(obj)
        except IntegrityError as exc:
            if _is_sku_code_conflict(exc):
                raise AppError(
                    "SKU 编码重复",
                    code=ErrorCode.SKU_CODE_DUPLICATED,
                    data={"sku_code": obj.sku_code},
                ) from exc
            raise

    async def list_for_spu(self, spu_id: int) -> list[Sku]:
        stmt = self.base_select().where(Sku.spu_id == spu_id).order_by(Sku.id.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_for_spu(self, spu_id: int) -> int:
        stmt = select(func.count()).select_from(Sku).where(Sku.spu_id == spu_id, Sku.deleted_at.is_(None))
        return int((await self.session.execute(stmt)).scalar_one())

    async def counts_for(self, spu_ids: list[int]) -> dict[int, int]:
        if not spu_ids:
            return {}
        stmt = (
            select(Sku.spu_id, func.count())
            .where(Sku.spu_id.in_(spu_ids), Sku.deleted_at.is_(None))
            .group_by(Sku.spu_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {int(spu_id): int(count) for spu_id, count in rows}

    async def get_by_code(self, sku_code: str) -> Sku | None:
        stmt = self.base_select().where(Sku.sku_code == sku_code)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def code_taken(self, sku_code: str, *, exclude_id: int | None = None) -> bool:
        stmt = self.base_select().where(Sku.sku_code == sku_code)
        if exclude_id is not None:
            stmt = stmt.where(Sku.id != exclude_id)
        found = (await self.session.execute(stmt)).scalar_one_or_none()
        return found is not None

    async def list_for_export(
        self,
        *,
        limit: int,
        status: str | None,
        title: str | None,
    ) -> list[tuple[Sku, Spu]]:
        stmt = (
            select(Sku, Spu)
            .join(Spu, Spu.id == Sku.spu_id)
            .where(Sku.deleted_at.is_(None), Spu.deleted_at.is_(None))
            .order_by(Sku.id.desc())
            .limit(limit)
        )
        if status:
            stmt = stmt.where(Spu.status == status)
        if title:
            stmt = stmt.where(Spu.title.ilike(_like(title), escape="\\"))
        rows = (await self.session.execute(stmt)).all()
        return [(sku, spu) for sku, spu in rows]


class ProductImageRepository(BaseRepository[ProductImage]):
    model = ProductImage

    async def list_for_spu(self, spu_id: int) -> list[ProductImage]:
        stmt = (
            self.base_select()
            .where(ProductImage.spu_id == spu_id)
            .order_by(ProductImage.sort.asc(), ProductImage.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())
