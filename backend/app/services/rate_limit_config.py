"""平台限流配额。表里的一行覆盖 ``quotas.py``，没有行就继续用代码默认值。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import RateLimitSpec
from app.adapters.quotas import quota_for
from app.adapters.registry import SUPPORTED_PLATFORMS
from app.core.errors import ParamInvalidError, PlatformUnsupportedError
from app.models.config import PlatformRateLimit
from app.repositories.rate_limit import PlatformRateLimitRepository

_DIMENSIONS = frozenset({"shop", "per_shop", "app", "per_app"})


@dataclass(frozen=True, slots=True)
class StoredQuota:
    qps: int
    burst: int
    dimension: str
    batch_limit: int
    daily_quota: int | None = None
    concurrency: int | None = None


@dataclass(frozen=True, slots=True)
class RateLimitView:
    platform_code: str
    spec: RateLimitSpec
    source: str
    row_id: int | None
    updated_at: datetime | None


def effective_spec(platform: str, stored: StoredQuota | None) -> RateLimitSpec:
    """没有配置行时用代码默认值，有行时整行替换。"""
    if stored is None:
        return quota_for(platform)
    return RateLimitSpec(
        qps=stored.qps,
        burst=stored.burst,
        dimension=stored.dimension,
        batch_limit=stored.batch_limit,
        daily_quota=stored.daily_quota,
        concurrency=stored.concurrency,
    )


def parse_quota(
    *,
    platform_code: str,
    dimension: str,
    qps: int,
    burst: int,
    batch_limit: int,
    daily_quota: int | None,
    concurrency: int | None,
) -> tuple[str, StoredQuota]:
    code = platform_code.strip().lower()
    if code not in SUPPORTED_PLATFORMS:
        raise PlatformUnsupportedError()
    dim = dimension.strip().lower()
    if dim not in _DIMENSIONS:
        raise ParamInvalidError("限流维度只能是 shop、per_shop、app、per_app")
    if qps < 1 or burst < 1 or batch_limit < 1:
        raise ParamInvalidError("每秒请求、突发和批量上限都必须大于 0")
    if daily_quota is not None and daily_quota < 1:
        raise ParamInvalidError("日配额必须大于 0")
    if concurrency is not None and concurrency < 1:
        raise ParamInvalidError("建议并发必须大于 0")
    return code, StoredQuota(
        qps=qps,
        burst=burst,
        dimension=dim,
        batch_limit=batch_limit,
        daily_quota=daily_quota,
        concurrency=concurrency,
    )


def _stored_from_row(row: PlatformRateLimit) -> StoredQuota:
    return StoredQuota(
        qps=row.qps,
        burst=row.burst,
        dimension=row.dimension,
        batch_limit=row.batch_limit,
        daily_quota=row.daily_quota,
        concurrency=row.concurrency,
    )


class RateLimitConfigService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = PlatformRateLimitRepository(session)

    async def list_effective(self) -> list[RateLimitView]:
        stored = {row.platform_code: row for row in await self.repo.list_rows()}
        views: list[RateLimitView] = []
        for code in SUPPORTED_PLATFORMS:
            row = stored.get(code)
            views.append(
                RateLimitView(
                    platform_code=code,
                    spec=effective_spec(code, None if row is None else _stored_from_row(row)),
                    source="default" if row is None else "table",
                    row_id=None if row is None else row.id,
                    updated_at=None if row is None else row.updated_at,
                )
            )
        return views

    async def resolve(self, platform: str) -> RateLimitSpec:
        code = platform.strip().lower()
        if code not in SUPPORTED_PLATFORMS:
            raise PlatformUnsupportedError()
        row = await self.repo.get_by_platform(code)
        return effective_spec(code, None if row is None else _stored_from_row(row))

    async def save(
        self,
        *,
        platform_code: str,
        dimension: str,
        qps: int,
        burst: int,
        batch_limit: int,
        daily_quota: int | None,
        concurrency: int | None,
        user_id: int,
    ) -> PlatformRateLimit:
        code, quota = parse_quota(
            platform_code=platform_code,
            dimension=dimension,
            qps=qps,
            burst=burst,
            batch_limit=batch_limit,
            daily_quota=daily_quota,
            concurrency=concurrency,
        )
        return await self.repo.save(
            platform_code=code,
            dimension=quota.dimension,
            qps=quota.qps,
            burst=quota.burst,
            batch_limit=quota.batch_limit,
            concurrency=quota.concurrency,
            daily_quota=quota.daily_quota,
            user_id=user_id,
        )


__all__ = [
    "RateLimitConfigService",
    "RateLimitView",
    "StoredQuota",
    "effective_spec",
    "parse_quota",
]
