"""Listing 映射与类目模板。跨租户 ID 由仓储返回 404。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbSession, Identity, require_permission
from app.core.pagination import MAX_PAGE_SIZE, PageData
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.listing import (
    CategoryMappingCreate,
    CategoryMappingPatch,
    CategoryMappingView,
    ListingCreate,
    ListingPatch,
    ListingShopOption,
    ListingView,
)
from app.schemas.listing_batch import (
    ListingBatchView,
    PriceBatchCreate,
    PriceBatchPreview,
    PricePreview,
    PublishBatchCreate,
)
from app.schemas.shop import PlatformSiteCatalog
from app.services.listing import ListingService
from app.services.listing_batch import ListingBatchService

router = APIRouter(tags=["Listing"])

ProductReader = Annotated[Identity, Depends(require_permission(Perm.PRODUCT_READ))]
ProductWriter = Annotated[Identity, Depends(require_permission(Perm.PRODUCT_WRITE))]


@router.get("/category-mappings", response_model=ApiResponse[PageData[CategoryMappingView]], summary="类目模板列表")
async def list_category_mappings(
    identity: ProductReader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    platform_code: Annotated[str | None, Query(max_length=32)] = None,
    site_code: Annotated[str | None, Query(max_length=8)] = None,
    q: Annotated[str | None, Query(max_length=128)] = None,
) -> ApiResponse[PageData[CategoryMappingView]]:
    del identity
    data = await ListingService(session).list_templates(
        limit=limit,
        cursor=cursor,
        platform_code=platform_code,
        site_code=site_code,
        keyword=q,
    )
    return ok(data)


@router.get(
    "/category-mappings/catalog",
    response_model=ApiResponse[list[PlatformSiteCatalog]],
    summary="可保存模板的平台与站点",
)
async def category_catalog(identity: ProductReader, session: DbSession) -> ApiResponse[list[PlatformSiteCatalog]]:
    del identity
    return ok(ListingService(session).platform_catalog())


@router.post("/category-mappings", response_model=ApiResponse[CategoryMappingView], summary="保存类目模板")
async def create_category_mapping(
    payload: CategoryMappingCreate,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CategoryMappingView]:
    data = await ListingService(session).create_template(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="类目模板已保存")


@router.patch(
    "/category-mappings/{template_id}",
    response_model=ApiResponse[CategoryMappingView],
    summary="更新类目模板",
)
async def update_category_mapping(
    template_id: int,
    payload: CategoryMappingPatch,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CategoryMappingView]:
    data = await ListingService(session).update_template(template_id, payload, actor_id=identity.user.id)
    return ok(data, message="类目模板已更新")


@router.get("/listings", response_model=ApiResponse[PageData[ListingView]], summary="Listing 映射列表")
async def list_listings(
    identity: ProductReader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    sku_id: Annotated[int | None, Query()] = None,
    shop_id: Annotated[int | None, Query()] = None,
    platform_product_id: Annotated[str | None, Query(max_length=128)] = None,
    status: Annotated[str | None, Query(max_length=32)] = None,
) -> ApiResponse[PageData[ListingView]]:
    del identity
    data = await ListingService(session).list_listings(
        limit=limit,
        cursor=cursor,
        sku_id=sku_id,
        shop_id=shop_id,
        platform_product_id=platform_product_id,
        status=status,
    )
    return ok(data)


@router.get("/listings/shops", response_model=ApiResponse[list[ListingShopOption]], summary="可映射的店铺")
async def listing_shops(identity: ProductReader, session: DbSession) -> ApiResponse[list[ListingShopOption]]:
    del identity
    return ok(await ListingService(session).list_shop_options())


@router.post("/listings", response_model=ApiResponse[ListingView], summary="创建 Listing 映射")
async def create_listing(
    payload: ListingCreate,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ListingView]:
    data = await ListingService(session).create_listing(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="Listing 已映射")


@router.get("/listings/{listing_id}", response_model=ApiResponse[ListingView], summary="Listing 详情")
async def get_listing(listing_id: int, identity: ProductReader, session: DbSession) -> ApiResponse[ListingView]:
    del identity
    return ok(await ListingService(session).get_listing(listing_id))


@router.patch("/listings/{listing_id}", response_model=ApiResponse[ListingView], summary="更新 Listing 映射")
async def update_listing(
    listing_id: int,
    payload: ListingPatch,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ListingView]:
    data = await ListingService(session).update_listing(listing_id, payload, actor_id=identity.user.id)
    return ok(data, message="Listing 已更新")


def _enqueue(kind: str, batch_id: int, tenant_id: int, status: str) -> None:
    if status != "PENDING":
        return
    from app.tasks.batch import enqueue_prices, enqueue_publish

    if kind == "PUBLISH":
        enqueue_publish(batch_id, tenant_id)
        return
    enqueue_prices(batch_id, tenant_id)


@router.post("/listing-batches/publish", response_model=ApiResponse[ListingBatchView], summary="批量刊登")
async def publish_listings(
    payload: PublishBatchCreate,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ListingBatchView]:
    data = await ListingBatchService(session).accept_publish(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    _enqueue("PUBLISH", data.id, identity.tenant.id, data.status)
    return ok(data, message="刊登批次已受理")


@router.post(
    "/listing-batches/prices/preview",
    response_model=ApiResponse[PricePreview],
    summary="批量改价预览",
)
async def preview_listing_prices(
    payload: PriceBatchPreview,
    identity: ProductWriter,
    session: DbSession,
) -> ApiResponse[PricePreview]:
    del identity
    return ok(await ListingBatchService(session).preview_prices(payload))


@router.post("/listing-batches/prices", response_model=ApiResponse[ListingBatchView], summary="批量改价")
async def reprice_listings(
    payload: PriceBatchCreate,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ListingBatchView]:
    data = await ListingBatchService(session).accept_prices(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    _enqueue("PRICE", data.id, identity.tenant.id, data.status)
    return ok(data, message="改价批次已受理")


@router.get("/listing-batches/{batch_id}", response_model=ApiResponse[ListingBatchView], summary="批次进度")
async def get_listing_batch(
    batch_id: int,
    identity: ProductReader,
    session: DbSession,
) -> ApiResponse[ListingBatchView]:
    del identity
    return ok(await ListingBatchService(session).get_batch(batch_id))
