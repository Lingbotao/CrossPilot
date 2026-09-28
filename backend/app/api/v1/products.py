"""商品库接口。跨租户 ID 由仓储返回 404。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbSession, Identity, require_permission
from app.core.pagination import MAX_PAGE_SIZE, PageData
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.product import SkuPatch, SkuView, SkuWrite, SpuCreate, SpuDetail, SpuListItem, SpuPatch
from app.services.product import ProductService

router = APIRouter(tags=["商品"])

ProductReader = Annotated[Identity, Depends(require_permission(Perm.PRODUCT_READ))]
ProductWriter = Annotated[Identity, Depends(require_permission(Perm.PRODUCT_WRITE))]


@router.get("/spus", response_model=ApiResponse[PageData[SpuListItem]], summary="商品列表")
async def list_spus(
    identity: ProductReader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    status: Annotated[str | None, Query(max_length=32)] = None,
    q: Annotated[str | None, Query(max_length=128)] = None,
) -> ApiResponse[PageData[SpuListItem]]:
    del identity
    data = await ProductService(session).list_spus(limit=limit, cursor=cursor, status=status, title=q)
    return ok(data)


@router.post("/spus", response_model=ApiResponse[SpuDetail], summary="创建商品")
async def create_spu(
    payload: SpuCreate,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[SpuDetail]:
    data = await ProductService(session).create_spu(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        can_view_cost=identity.can_view_cost,
    )
    return ok(data, message="商品已创建")


@router.get("/spus/{spu_id}", response_model=ApiResponse[SpuDetail], summary="商品详情")
async def get_spu(spu_id: int, identity: ProductReader, session: DbSession) -> ApiResponse[SpuDetail]:
    data = await ProductService(session).get_spu(spu_id, can_view_cost=identity.can_view_cost)
    return ok(data)


@router.patch("/spus/{spu_id}", response_model=ApiResponse[SpuDetail], summary="更新商品")
async def update_spu(
    spu_id: int,
    payload: SpuPatch,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[SpuDetail]:
    data = await ProductService(session).update_spu(
        spu_id,
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        can_view_cost=identity.can_view_cost,
    )
    return ok(data, message="商品已更新")


@router.post("/spus/{spu_id}/skus", response_model=ApiResponse[SkuView], summary="新增变体")
async def add_sku(
    spu_id: int,
    payload: SkuWrite,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[SkuView]:
    data = await ProductService(session).add_sku(
        spu_id,
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        can_view_cost=identity.can_view_cost,
    )
    return ok(data, message="变体已添加")


@router.patch("/skus/{sku_id}", response_model=ApiResponse[SkuView], summary="更新变体")
async def update_sku(
    sku_id: int,
    payload: SkuPatch,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[SkuView]:
    data = await ProductService(session).update_sku(
        sku_id,
        payload,
        actor_id=identity.user.id,
        can_view_cost=identity.can_view_cost,
    )
    return ok(data, message="变体已更新")
