"""平台限流配额。读和改都要系统权限，订单运营改不了 QPS。"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header

from app.core.deps import DbSession, Identity, require_permission
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.models.config import PlatformRateLimit
from app.schemas.rate_limit import RateLimitResponse, RateLimitSource, UpsertRateLimitRequest
from app.services.rate_limit_config import RateLimitConfigService, RateLimitView

router = APIRouter(tags=["平台限流"])

QuotaReader = Annotated[Identity, Depends(require_permission(Perm.SYSTEM_READ))]
QuotaWriter = Annotated[Identity, Depends(require_permission(Perm.SYSTEM_WRITE))]


def _view(item: RateLimitView) -> RateLimitResponse:
    spec = item.spec
    return RateLimitResponse(
        id=item.row_id,
        platform_code=item.platform_code,
        dimension=spec.dimension,
        qps=spec.qps,
        burst=spec.burst,
        batch_limit=spec.batch_limit,
        daily_quota=spec.daily_quota,
        concurrency=spec.concurrency,
        source=cast(RateLimitSource, item.source),
        updated_at=item.updated_at,
    )


def _saved(row: PlatformRateLimit) -> RateLimitResponse:
    return RateLimitResponse(
        id=row.id,
        platform_code=row.platform_code,
        dimension=row.dimension,
        qps=row.qps,
        burst=row.burst,
        batch_limit=row.batch_limit,
        daily_quota=row.daily_quota,
        concurrency=row.concurrency,
        source="table",
        updated_at=row.updated_at,
    )


@router.get(
    "/platform-rate-limits",
    response_model=ApiResponse[list[RateLimitResponse]],
    summary="四个平台的有效限流配额",
)
async def list_rate_limits(
    identity: QuotaReader,
    session: DbSession,
) -> ApiResponse[list[RateLimitResponse]]:
    del identity
    rows = await RateLimitConfigService(session).list_effective()
    return ok([_view(row) for row in rows])


@router.put(
    "/platform-rate-limits",
    response_model=ApiResponse[RateLimitResponse],
    summary="覆盖一个平台的限流配额",
)
async def upsert_rate_limit(
    payload: UpsertRateLimitRequest,
    identity: QuotaWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[RateLimitResponse]:
    row = await RateLimitConfigService(session).save(
        platform_code=payload.platform_code,
        dimension=payload.dimension,
        qps=payload.qps,
        burst=payload.burst,
        batch_limit=payload.batch_limit,
        daily_quota=payload.daily_quota,
        concurrency=payload.concurrency,
        user_id=identity.user.id,
    )
    return ok(_saved(row))


__all__ = ["router"]
