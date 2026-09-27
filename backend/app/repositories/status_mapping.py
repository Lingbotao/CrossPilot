"""平台状态映射。这张表没有 tenant_id，查询不会套租户条件。"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.db.snowflake import next_snowflake_id
from app.models.config import PlatformStatusMapping
from app.repositories.base import BaseRepository


class PlatformStatusMappingRepository(BaseRepository[PlatformStatusMapping]):
    model = PlatformStatusMapping

    async def list_rows(self, platform: str | None) -> list[PlatformStatusMapping]:
        stmt = self.base_select().order_by(
            PlatformStatusMapping.platform_code,
            PlatformStatusMapping.platform_status,
        )
        if platform is not None:
            stmt = stmt.where(PlatformStatusMapping.platform_code == platform)
        return list((await self.session.execute(stmt)).scalars().all())

    async def as_dict(self, platform: str) -> dict[str, str]:
        rows = await self.list_rows(platform)
        return {row.platform_status: row.unified_status for row in rows}

    async def insert_missing(self, platform: str, defaults: dict[str, str]) -> None:
        if not defaults:
            return
        values = [
            {
                "id": next_snowflake_id(),
                "platform_code": platform,
                "platform_status": status,
                "unified_status": unified,
            }
            for status, unified in sorted(defaults.items())
        ]
        stmt = (
            pg_insert(PlatformStatusMapping)
            .values(values)
            .on_conflict_do_nothing(constraint="uq_platform_status_mapping_platform_code_platform_status")
        )
        await self.session.execute(stmt)

    async def save(
        self,
        *,
        platform_code: str,
        platform_status: str,
        unified_status: str,
        user_id: int,
    ) -> PlatformStatusMapping:
        stmt = self.base_select().where(
            PlatformStatusMapping.platform_code == platform_code,
            PlatformStatusMapping.platform_status == platform_status,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            row.unified_status = unified_status
            row.updated_by = user_id
            await self.session.flush()
            return row
        row = PlatformStatusMapping(
            platform_code=platform_code,
            platform_status=platform_status,
            unified_status=unified_status,
            created_by=user_id,
            updated_by=user_id,
        )
        self.session.add(row)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError:
            raced = (await self.session.execute(stmt)).scalar_one()
            raced.unified_status = unified_status
            raced.updated_by = user_id
            await self.session.flush()
            return raced
        return row


__all__ = ["PlatformStatusMappingRepository"]
