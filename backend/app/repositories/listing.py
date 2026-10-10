"""Listing 与类目模板查询。软删除行不占平台 SKU 和本地类目编码。"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError, ErrorCode
from app.db.tenant_filter import SKIP_FLAG
from app.models.listing import CategoryMapping, Listing, ListingBatch, ListingBatchItem, ListingDiff
from app.repositories.base import BaseRepository


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _unique_violation(exc: IntegrityError, index_name: str) -> bool:
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
    text = str(orig or exc)
    return (code == "23505" or "unique" in text.lower()) and index_name in text


class CategoryMappingRepository(BaseRepository[CategoryMapping]):
    model = CategoryMapping

    async def add(self, obj: CategoryMapping) -> CategoryMapping:
        try:
            return await super().add(obj)
        except IntegrityError as exc:
            self._reraise_duplicate(exc, obj.local_category_code)
            raise

    async def flush_unique(self, local_category_code: str) -> None:
        try:
            await self.session.flush()
        except IntegrityError as exc:
            self._reraise_duplicate(exc, local_category_code)
            raise

    @staticmethod
    def _reraise_duplicate(exc: IntegrityError, local_category_code: str) -> None:
        if _unique_violation(exc, "uq_category_mapping_tenant_platform_site_code_active"):
            raise AppError(
                "该类目模板已存在",
                code=ErrorCode.CATEGORY_TEMPLATE_DUPLICATED,
                data={"local_category_code": local_category_code},
            ) from exc

    async def list_cursor(
        self,
        *,
        limit: int,
        before_id: int | None,
        platform_code: str | None,
        site_code: str | None,
        keyword: str | None,
    ) -> list[CategoryMapping]:
        stmt = self.base_select()
        if before_id is not None:
            stmt = stmt.where(CategoryMapping.id < before_id)
        if platform_code:
            stmt = stmt.where(CategoryMapping.platform_code == platform_code)
        if site_code:
            stmt = stmt.where(CategoryMapping.site_code == site_code)
        if keyword:
            pattern = _like(keyword)
            stmt = stmt.where(
                or_(
                    CategoryMapping.name.ilike(pattern, escape="\\"),
                    CategoryMapping.local_category_code.ilike(pattern, escape="\\"),
                )
            )
        stmt = stmt.order_by(CategoryMapping.id.desc()).limit(limit + 1)
        return list((await self.session.execute(stmt)).scalars().all())

    async def code_taken(
        self,
        *,
        platform_code: str,
        site_code: str,
        local_category_code: str,
        exclude_id: int | None = None,
    ) -> bool:
        stmt = self.base_select().where(
            CategoryMapping.platform_code == platform_code,
            CategoryMapping.site_code == site_code,
            CategoryMapping.local_category_code == local_category_code,
        )
        if exclude_id is not None:
            stmt = stmt.where(CategoryMapping.id != exclude_id)
        found = (await self.session.execute(stmt)).scalar_one_or_none()
        return found is not None


class ListingRepository(BaseRepository[Listing]):
    model = Listing

    async def add(self, obj: Listing) -> Listing:
        try:
            return await super().add(obj)
        except IntegrityError as exc:
            self._reraise_platform_sku(exc, obj.platform_sku_id)
            raise

    async def flush_unique(self, platform_sku_id: str | None) -> None:
        try:
            await self.session.flush()
        except IntegrityError as exc:
            self._reraise_platform_sku(exc, platform_sku_id)
            raise

    async def list_cursor(
        self,
        *,
        limit: int,
        before_id: int | None,
        sku_id: int | None,
        shop_id: int | None,
        platform_product_id: str | None,
        status: str | None,
    ) -> list[Listing]:
        stmt = self.base_select()
        if before_id is not None:
            stmt = stmt.where(Listing.id < before_id)
        if sku_id is not None:
            stmt = stmt.where(Listing.sku_id == sku_id)
        if shop_id is not None:
            stmt = stmt.where(Listing.shop_id == shop_id)
        if platform_product_id:
            stmt = stmt.where(Listing.platform_product_id == platform_product_id)
        if status:
            stmt = stmt.where(Listing.status == status)
        stmt = stmt.order_by(Listing.id.desc()).limit(limit + 1)
        return list((await self.session.execute(stmt)).scalars().all())

    async def linked_pair(self, sku_id: int, shop_id: int) -> Listing | None:
        stmt = (
            self.base_select()
            .where(Listing.sku_id == sku_id, Listing.shop_id == shop_id, Listing.status == "LINKED")
            .order_by(Listing.id.desc())
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def open_pair(self, sku_id: int, shop_id: int) -> Listing | None:
        stmt = (
            self.base_select()
            .where(Listing.sku_id == sku_id, Listing.shop_id == shop_id, Listing.status != "LINKED")
            .order_by(Listing.id.desc())
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def get_many(self, entity_ids: list[int]) -> list[Listing]:
        if not entity_ids:
            return []
        stmt = self.base_select().where(Listing.id.in_(entity_ids))
        return list((await self.session.execute(stmt)).scalars().all())

    async def linked_sku_id(self, shop_id: int, platform_sku_id: str) -> int | None:
        stmt = self.base_select().where(
            Listing.shop_id == shop_id,
            Listing.platform_sku_id == platform_sku_id,
            Listing.status == "LINKED",
        )
        row = (await self.session.execute(stmt)).scalars().first()
        return None if row is None else int(row.sku_id)

    async def list_linked(self, *, limit: int) -> list[Listing]:
        stmt = self.base_select().where(Listing.status == "LINKED").order_by(Listing.id.asc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_linked_for_sku(self, sku_id: int) -> list[Listing]:
        stmt = (
            self.base_select()
            .where(
                Listing.sku_id == sku_id,
                Listing.status == "LINKED",
                Listing.platform_sku_id.is_not(None),
            )
            .order_by(Listing.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def tenant_ids_with_linked(self) -> list[int]:
        """系统巡检用。显式跳过租户过滤，调用方再按租户逐个进入上下文。"""

        stmt = (
            select(Listing.tenant_id)
            .where(Listing.status == "LINKED", Listing.deleted_at.is_(None))
            .distinct()
            .execution_options(**{SKIP_FLAG: True})
        )
        return [int(row) for row in (await self.session.execute(stmt)).scalars().all()]

    async def platform_sku_taken(self, shop_id: int, platform_sku_id: str, *, exclude_id: int | None = None) -> bool:
        stmt = self.base_select().where(Listing.shop_id == shop_id, Listing.platform_sku_id == platform_sku_id)
        if exclude_id is not None:
            stmt = stmt.where(Listing.id != exclude_id)
        found = (await self.session.execute(stmt)).scalar_one_or_none()
        return found is not None

    @staticmethod
    def _reraise_platform_sku(exc: IntegrityError, platform_sku_id: str | None) -> None:
        if _unique_violation(exc, "uq_listing_tenant_shop_platform_sku_active"):
            raise AppError(
                "该店铺的平台 SKU 已映射到本地商品",
                code=ErrorCode.LISTING_PLATFORM_SKU_TAKEN,
                data={"platform_sku_id": platform_sku_id},
            ) from exc


class ListingBatchRepository(BaseRepository[ListingBatch]):
    model = ListingBatch


class ListingBatchItemRepository(BaseRepository[ListingBatchItem]):
    model = ListingBatchItem

    async def list_for_batch(self, batch_id: int) -> list[ListingBatchItem]:
        stmt = self.base_select().where(ListingBatchItem.batch_id == batch_id).order_by(ListingBatchItem.id.asc())
        return list((await self.session.execute(stmt)).scalars().all())


class ListingDiffRepository(BaseRepository[ListingDiff]):
    model = ListingDiff

    async def pending(self, listing_id: int, field_name: str) -> ListingDiff | None:
        stmt = self.base_select().where(
            ListingDiff.listing_id == listing_id,
            ListingDiff.field_name == field_name,
            ListingDiff.status == "PENDING",
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def latest_closed(self, listing_id: int, field_name: str) -> ListingDiff | None:
        stmt = (
            self.base_select()
            .where(
                ListingDiff.listing_id == listing_id,
                ListingDiff.field_name == field_name,
                ListingDiff.status != "PENDING",
            )
            .order_by(ListingDiff.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_cursor(self, *, limit: int, before_id: int | None, status: str | None) -> list[ListingDiff]:
        stmt = self.base_select()
        if status:
            stmt = stmt.where(ListingDiff.status == status)
        if before_id is not None:
            stmt = stmt.where(ListingDiff.id < before_id)
        stmt = stmt.order_by(ListingDiff.id.desc()).limit(limit + 1)
        return list((await self.session.execute(stmt)).scalars().all())
