"""租户上下文接口（PRD 10.3 批次 1）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import CurrentIdentity, DbSession
from app.core.response import ApiResponse, ok
from app.schemas.auth import TenantCurrentResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/tenants", tags=["租户"])


@router.get("/current", response_model=ApiResponse[TenantCurrentResponse], summary="当前租户上下文")
async def current_tenant(identity: CurrentIdentity, session: DbSession) -> ApiResponse[TenantCurrentResponse]:
    """当前租户信息 + 我的角色 + 权限点 + 数据范围。

    前端启动时会依赖本接口渲染菜单与按钮 ——
    **但后端仍然会对每个请求独立校验权限**，前端隐藏只是体验优化。
    """
    service = AuthService(session)
    context = await service.build_tenant_context(identity.user, identity.tenant, identity.membership)
    return ok(context)
