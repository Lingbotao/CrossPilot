"""平台与授权域 Repository。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from app.models.platform import PlatformApiLog, Shop, ShopCredential, SyncTask
from app.repositories.base import BaseRepository


class ShopRepository(BaseRepository[Shop]):
    model = Shop

    async def list_page(self, *, page: int, page_size: int) -> tuple[list[Shop], int]:
        count_stmt = select(func.count()).select_from(Shop).where(Shop.deleted_at.is_(None))
        total = int((await self.session.execute(count_stmt)).scalar_one())
        stmt = self.base_select().order_by(Shop.id.desc()).offset((page - 1) * page_size).limit(page_size)
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total

    async def find_active(self, *, platform_code: str, site_code: str, platform_shop_id: str) -> Shop | None:
        stmt = self.base_select().where(
            Shop.platform_code == platform_code,
            Shop.site_code == site_code,
            Shop.platform_shop_id == platform_shop_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class ShopCredentialRepository(BaseRepository[ShopCredential]):
    model = ShopCredential

    async def get_by_shop_id(self, shop_id: int) -> ShopCredential | None:
        stmt = self.base_select().where(ShopCredential.shop_id == shop_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def map_by_shop_ids(self, shop_ids: list[int]) -> dict[int, ShopCredential]:
        if not shop_ids:
            return {}
        stmt = self.base_select().where(ShopCredential.shop_id.in_(shop_ids))
        rows = (await self.session.execute(stmt)).scalars().all()
        return {row.shop_id: row for row in rows}


class SyncTaskRepository(BaseRepository[SyncTask]):
    model = SyncTask

    async def list_cursor(
        self,
        *,
        limit: int,
        before_id: int | None,
        shop_id: int | None,
        module: str | None,
        status: int | None,
        created_from: datetime | None,
        created_to: datetime | None,
    ) -> list[SyncTask]:
        stmt = self.base_select()
        if before_id is not None:
            stmt = stmt.where(SyncTask.id < before_id)
        if shop_id is not None:
            stmt = stmt.where(SyncTask.shop_id == shop_id)
        if module is not None:
            stmt = stmt.where(SyncTask.module == module)
        if status is not None:
            stmt = stmt.where(SyncTask.status == status)
        if created_from is not None:
            stmt = stmt.where(SyncTask.created_at >= created_from)
        if created_to is not None:
            stmt = stmt.where(SyncTask.created_at <= created_to)
        stmt = stmt.order_by(SyncTask.id.desc()).limit(limit + 1)
        return list((await self.session.execute(stmt)).scalars().all())


class PlatformApiLogRepository(BaseRepository[PlatformApiLog]):
    model = PlatformApiLog


__all__ = [
    "PlatformApiLogRepository",
    "ShopCredentialRepository",
    "ShopRepository",
    "SyncTaskRepository",
]
