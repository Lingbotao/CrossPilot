"""广告只读接口（F8-01~05）。没有投放写操作。亏损和扣广告净利要求成本可见。"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbSession, Identity, require_cost_visibility, require_permission
from app.core.errors import NotFoundError, ParamInvalidError
from app.core.pagination import MAX_PAGE_SIZE, PageData
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.ads import (
    AdsCampaignDetail,
    AdsCampaignView,
    AdsKeywordView,
    AdsLossView,
    AdsOverviewView,
    AdsSkuProfitView,
    AdsSyncRequest,
    AdsSyncView,
)
from app.schemas.listing import parse_id
from app.services.ads import AdsService

router = APIRouter(prefix="/ads", tags=["广告"])

Reader = Annotated[Identity, Depends(require_permission(Perm.ADS_READ))]


async def _cost_reader(identity: Reader) -> Identity:
    return await require_cost_visibility(identity)


CostReader = Annotated[Identity, Depends(_cost_reader)]


@router.post("/sync", response_model=ApiResponse[AdsSyncView], summary="手动补拉广告")
async def sync_ads(
    payload: AdsSyncRequest,
    identity: Reader,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[AdsSyncView]:
    data = await AdsService(session).sync(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        idempotency_key=idempotency_key,
    )
    return ok(data)


@router.get("/overview", response_model=ApiResponse[AdsOverviewView], summary="广告总览")
async def ads_overview(
    identity: Reader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    shop_id: Annotated[str | None, Query()] = None,
    platform_code: Annotated[str | None, Query()] = None,
    campaign_id: Annotated[str | None, Query()] = None,
) -> ApiResponse[AdsOverviewView]:
    data = await AdsService(session).overview(
        date_from=date_from,
        date_to=date_to,
        shop_id=_optional_id(shop_id),
        platform_code=platform_code,
        campaign_id=_optional_id(campaign_id),
        visible=identity.can_view_cost,
    )
    return ok(data)


@router.get("/campaigns", response_model=ApiResponse[PageData[AdsCampaignView]], summary="广告活动")
async def list_campaigns(
    identity: Reader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    shop_id: Annotated[str | None, Query()] = None,
    platform_code: Annotated[str | None, Query()] = None,
    campaign_id: Annotated[str | None, Query()] = None,
) -> ApiResponse[PageData[AdsCampaignView]]:
    data = await AdsService(session).list_campaigns(
        limit=limit,
        cursor=cursor,
        date_from=date_from,
        date_to=date_to,
        shop_id=_optional_id(shop_id),
        platform_code=platform_code,
        campaign_id=_optional_id(campaign_id),
        visible=identity.can_view_cost,
    )
    return ok(data)


@router.get("/campaigns/{campaign_id}", response_model=ApiResponse[AdsCampaignDetail], summary="广告活动日指标")
async def campaign_detail(
    campaign_id: str,
    identity: Reader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
) -> ApiResponse[AdsCampaignDetail]:
    data = await AdsService(session).campaign_detail(
        _path_id(campaign_id),
        date_from=date_from,
        date_to=date_to,
        visible=identity.can_view_cost,
    )
    return ok(data)


@router.get("/keywords", response_model=ApiResponse[PageData[AdsKeywordView]], summary="关键词表现")
async def list_keywords(
    identity: Reader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    shop_id: Annotated[str | None, Query()] = None,
    platform_code: Annotated[str | None, Query()] = None,
    campaign_id: Annotated[str | None, Query()] = None,
    only_negative: Annotated[bool, Query()] = False,
) -> ApiResponse[PageData[AdsKeywordView]]:
    data = await AdsService(session).list_keywords(
        limit=limit,
        cursor=cursor,
        date_from=date_from,
        date_to=date_to,
        shop_id=_optional_id(shop_id),
        platform_code=platform_code,
        campaign_id=_optional_id(campaign_id),
        only_negative=only_negative,
        visible=identity.can_view_cost,
    )
    return ok(data)


@router.get("/loss", response_model=ApiResponse[PageData[AdsLossView]], summary="亏损广告")
async def list_loss(
    identity: CostReader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    shop_id: Annotated[str | None, Query()] = None,
    platform_code: Annotated[str | None, Query()] = None,
) -> ApiResponse[PageData[AdsLossView]]:
    del identity
    data = await AdsService(session).list_loss(
        limit=limit,
        cursor=cursor,
        date_from=date_from,
        date_to=date_to,
        shop_id=_optional_id(shop_id),
        platform_code=platform_code,
    )
    return ok(data)


@router.get("/sku-profit", response_model=ApiResponse[list[AdsSkuProfitView]], summary="扣除广告费后的 SKU 净利")
async def sku_profit(
    identity: CostReader,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    shop_id: Annotated[str | None, Query()] = None,
    platform_code: Annotated[str | None, Query()] = None,
) -> ApiResponse[list[AdsSkuProfitView]]:
    del identity
    data = await AdsService(session).sku_profit(
        date_from=date_from,
        date_to=date_to,
        shop_id=_optional_id(shop_id),
        platform_code=platform_code,
    )
    return ok(data)


def _optional_id(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return parse_id(value)
    except ValueError as exc:
        raise ParamInvalidError("ID 不合法") from exc


def _path_id(value: str) -> int:
    try:
        return parse_id(value)
    except ValueError as exc:
        raise NotFoundError() from exc
