"""平台限流配额。这张表没有 tenant_id，查询不会套租户条件。"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.models.config import PlatformRateLimit
from app.repositories.base import BaseRepository


class PlatformRateLimitRepository(BaseRepository[PlatformRateLimit]):
    model = PlatformRateLimit

    async def list_rows(self) -> list[PlatformRateLimit]:
        stmt = self.base_select().order_by(PlatformRateLimit.platform_code)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_platform(self, platform_code: str) -> PlatformRateLimit | None:
        stmt = self.base_select().where(PlatformRateLimit.platform_code == platform_code)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def save(
        self,
        *,
        platform_code: str,
        dimension: str,
        qps: int,
        burst: int,
        batch_limit: int,
        concurrency: int | None,
        daily_quota: int | None,
        stock_push_lag_seconds: int,
        user_id: int,
    ) -> PlatformRateLimit:
        stmt = self.base_select().where(PlatformRateLimit.platform_code == platform_code)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            row.dimension = dimension
            row.qps = qps
            row.burst = burst
            row.batch_limit = batch_limit
            row.concurrency = concurrency
            row.daily_quota = daily_quota
            row.stock_push_lag_seconds = stock_push_lag_seconds
            row.updated_by = user_id
            await self.session.flush()
            return row
        row = PlatformRateLimit(
            platform_code=platform_code,
            dimension=dimension,
            qps=qps,
            burst=burst,
            batch_limit=batch_limit,
            concurrency=concurrency,
            daily_quota=daily_quota,
            stock_push_lag_seconds=stock_push_lag_seconds,
            created_by=user_id,
            updated_by=user_id,
        )
        self.session.add(row)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError:
            raced = (await self.session.execute(stmt)).scalar_one()
            raced.dimension = dimension
            raced.qps = qps
            raced.burst = burst
            raced.batch_limit = batch_limit
            raced.concurrency = concurrency
            raced.daily_quota = daily_quota
            raced.stock_push_lag_seconds = stock_push_lag_seconds
            raced.updated_by = user_id
            await self.session.flush()
            return raced
        return row


__all__ = ["PlatformRateLimitRepository"]
