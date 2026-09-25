"""同步任务查询。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbSession, Identity, require_permission
from app.core.pagination import PageData
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.shop import SyncTaskResponse
from app.services.shop_service import ShopService

router = APIRouter(prefix="/sync-tasks", tags=["店铺授权"])

ShopReader = Annotated[Identity, Depends(require_permission(Perm.SHOP_READ))]


@router.get("", response_model=ApiResponse[PageData[SyncTaskResponse]], summary="同步任务列表")
async def list_sync_tasks(
    identity: ShopReader,
    session: DbSession,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    shop_id: int | None = None,
    module: str | None = None,
    status: int | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> ApiResponse[PageData[SyncTaskResponse]]:
    del identity
    return ok(
        await ShopService(session).list_tasks(
            cursor=cursor,
            limit=limit,
            shop_id=shop_id,
            module=module,
            status=status,
            created_from=created_from,
            created_to=created_to,
        )
    )


@router.get("/{task_id}", response_model=ApiResponse[SyncTaskResponse], summary="同步任务详情")
async def get_sync_task(task_id: int, identity: ShopReader, session: DbSession) -> ApiResponse[SyncTaskResponse]:
    del identity
    return ok(await ShopService(session).get_task(task_id))
