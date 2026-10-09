"""HS 编码检索与商品绑定。跨租户 SPU 与未知编码都返回 404。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbSession, Identity, require_permission
from app.core.errors import ParamInvalidError
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.hs_code import HsBindRequest, HsCodeHit, SpuHsBindingView
from app.schemas.listing import parse_id
from app.services.hs_code import HsCodeService

router = APIRouter(tags=["合规"])

ComplianceReader = Annotated[Identity, Depends(require_permission(Perm.COMPLIANCE_READ))]
ComplianceWriter = Annotated[Identity, Depends(require_permission(Perm.COMPLIANCE_WRITE))]


def _optional_spu_id(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    try:
        return parse_id(raw)
    except ValueError as exc:
        raise ParamInvalidError("商品 ID 不合法") from exc


@router.get("/hs-codes/search", response_model=ApiResponse[list[HsCodeHit]], summary="检索 HS 编码")
async def search_hs_codes(
    identity: ComplianceReader,
    session: DbSession,
    q: Annotated[str | None, Query(max_length=128)] = None,
    spu_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ApiResponse[list[HsCodeHit]]:
    del identity
    data = await HsCodeService(session).search(q=q, spu_id=_optional_spu_id(spu_id), limit=limit)
    return ok(data)


@router.get(
    "/spus/{spu_id}/hs-bindings",
    response_model=ApiResponse[list[SpuHsBindingView]],
    summary="商品各市场 HS 绑定",
)
async def list_spu_hs_bindings(
    spu_id: int,
    identity: ComplianceReader,
    session: DbSession,
) -> ApiResponse[list[SpuHsBindingView]]:
    del identity
    return ok(await HsCodeService(session).list_bindings(spu_id))


@router.post(
    "/spus/{spu_id}/hs-code",
    response_model=ApiResponse[SpuHsBindingView],
    summary="按市场绑定 HS 编码",
)
async def bind_spu_hs_code(
    spu_id: int,
    payload: HsBindRequest,
    identity: ComplianceWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[SpuHsBindingView]:
    data = await HsCodeService(session).bind(
        spu_id,
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="HS 编码已绑定")
