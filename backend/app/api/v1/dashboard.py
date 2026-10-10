"""经营看板（F10-01~08）。读汇总表；成本和利润对一线运营隐藏。"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbSession, Identity, require_cost_visibility, require_permission
from app.core.errors import ParamInvalidError
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.dashboard import (
    AdsRoiView,
    CostLineView,
    DashboardOverviewView,
    DashboardRebuildRequest,
    DashboardRebuildView,
    FulfillmentView,
    InventoryHealthView,
    PlatformCompareView,
    SkuRankingView,
    TrendView,
)
from app.schemas.listing import parse_id
from app.services.dashboard import DashboardService

router = APIRouter(prefix="/dashboard", tags=["看板"])

Reader = Annotated[Identity, Depends(require_permission(Perm.DASHBOARD_READ))]


async def _cost_reader(identity: Reader) -> Identity:
    return await require_cost_visibility(identity)


CostReader = Annotated[Identity, Depends(_cost_reader)]


def _optional_id(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return parse_id(value)
    except ValueError as exc:
        raise ParamInvalidError("店铺编号无效") from exc


@router.post("/rebuild", response_model=ApiResponse[DashboardRebuildView], summary="刷新看板汇总")
async def rebuild_dashboard(
    payload: DashboardRebuildRequest,
    identity: Reader,
    session: DbSession,
) -> ApiResponse[DashboardRebuildView]:
    data = await DashboardService(session).rebuild(
        date_from=payload.date_from,
        date_to=payload.date_to,
        book_currency=str(identity.tenant.default_currency).strip(),
    )
    return ok(data)


@router.get("/overview", response_model=ApiResponse[DashboardOverviewView], summary="经营总览")
async def dashboard_overview(
    identity: Reader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    shop_id: Annotated[str | None, Query()] = None,
    platform_code: Annotated[str | None, Query()] = None,
) -> ApiResponse[DashboardOverviewView]:
    data = await DashboardService(session).overview(
        date_from=date_from,
        date_to=date_to,
        shop_id=_optional_id(shop_id),
        platform_code=platform_code,
        visible=identity.can_view_cost,
    )
    return ok(data)


@router.get("/platform-comparison", response_model=ApiResponse[list[PlatformCompareView]], summary="平台对比")
async def platform_comparison(
    identity: Reader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    shop_id: Annotated[str | None, Query()] = None,
    platform_code: Annotated[str | None, Query()] = None,
) -> ApiResponse[list[PlatformCompareView]]:
    data = await DashboardService(session).platforms(
        date_from=date_from,
        date_to=date_to,
        shop_id=_optional_id(shop_id),
        platform_code=platform_code,
        visible=identity.can_view_cost,
    )
    return ok(data)


@router.get("/trends", response_model=ApiResponse[TrendView], summary="销售趋势")
async def dashboard_trends(
    identity: Reader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    shop_id: Annotated[str | None, Query()] = None,
    platform_code: Annotated[str | None, Query()] = None,
) -> ApiResponse[TrendView]:
    data = await DashboardService(session).trends(
        date_from=date_from,
        date_to=date_to,
        shop_id=_optional_id(shop_id),
        platform_code=platform_code,
        visible=identity.can_view_cost,
    )
    return ok(data)


@router.get("/sku-ranking", response_model=ApiResponse[SkuRankingView], summary="SKU 盈亏榜")
async def sku_ranking(
    identity: CostReader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    shop_id: Annotated[str | None, Query()] = None,
    platform_code: Annotated[str | None, Query()] = None,
) -> ApiResponse[SkuRankingView]:
    data = await DashboardService(session).ranking(
        date_from=date_from,
        date_to=date_to,
        shop_id=_optional_id(shop_id),
        platform_code=platform_code,
    )
    return ok(data)


@router.get("/cost-structure", response_model=ApiResponse[list[CostLineView]], summary="成本结构")
async def cost_structure(
    identity: CostReader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    shop_id: Annotated[str | None, Query()] = None,
    platform_code: Annotated[str | None, Query()] = None,
) -> ApiResponse[list[CostLineView]]:
    data = await DashboardService(session).costs(
        date_from=date_from,
        date_to=date_to,
        shop_id=_optional_id(shop_id),
        platform_code=platform_code,
    )
    return ok(data)


@router.get("/inventory-health", response_model=ApiResponse[InventoryHealthView], summary="库存健康")
async def inventory_health(
    identity: Reader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
) -> ApiResponse[InventoryHealthView]:
    data = await DashboardService(session).inventory(
        date_from=date_from,
        date_to=date_to,
        visible=identity.can_view_cost,
    )
    return ok(data)


@router.get("/ads-roi", response_model=ApiResponse[list[AdsRoiView]], summary="广告 ROI")
async def ads_roi(
    identity: Reader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    shop_id: Annotated[str | None, Query()] = None,
    platform_code: Annotated[str | None, Query()] = None,
) -> ApiResponse[list[AdsRoiView]]:
    data = await DashboardService(session).ads_roi(
        date_from=date_from,
        date_to=date_to,
        shop_id=_optional_id(shop_id),
        platform_code=platform_code,
        visible=identity.can_view_cost,
    )
    return ok(data)


@router.get("/fulfillment", response_model=ApiResponse[list[FulfillmentView]], summary="订单履约")
async def fulfillment(
    _identity: Reader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    shop_id: Annotated[str | None, Query()] = None,
    platform_code: Annotated[str | None, Query()] = None,
) -> ApiResponse[list[FulfillmentView]]:
    data = await DashboardService(session).fulfillment(
        date_from=date_from,
        date_to=date_to,
        shop_id=_optional_id(shop_id),
        platform_code=platform_code,
    )
    return ok(data)
