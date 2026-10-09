"""落地成本计算与费用规则。成本接口同时要求费用权限和成本可见性。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbSession, Identity, require_cost_visibility, require_permission
from app.core.errors import ParamInvalidError
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.landed_cost import CalcRequest, CalcView, CompareRequest, CompareView, FeeCreate, FeeView
from app.schemas.listing import parse_id
from app.schemas.locale import clean_market
from app.services.landed_cost import LandedCostService

router = APIRouter(prefix="/landed-cost", tags=["落地成本"])

Reader = Annotated[Identity, Depends(require_permission(Perm.LANDED_COST_READ))]
Writer = Annotated[Identity, Depends(require_permission(Perm.LANDED_COST_CALC))]
Visible = Annotated[Identity, Depends(require_cost_visibility)]


def _market(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    try:
        return clean_market(raw)
    except ValueError as exc:
        raise ParamInvalidError("市场不在支持列表中") from exc


@router.get("/fees", response_model=ApiResponse[list[FeeView]], summary="落地成本费用规则")
async def list_fees(
    identity: Reader,
    _: Visible,
    session: DbSession,
    market: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ApiResponse[list[FeeView]]:
    del identity
    data = await LandedCostService(session).list_fees(market=_market(market), limit=limit)
    return ok(data)


@router.post("/fees", response_model=ApiResponse[FeeView], summary="新增落地成本费用版本")
async def create_fee(
    payload: FeeCreate,
    identity: Writer,
    _: Visible,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[FeeView]:
    data = await LandedCostService(session).create_fee(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data)


@router.post("/fees/{fee_id}/retire", response_model=ApiResponse[FeeView], summary="停用落地成本费用")
async def retire_fee(
    fee_id: str,
    identity: Writer,
    _: Visible,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[FeeView]:
    try:
        parsed = parse_id(fee_id)
    except ValueError as exc:
        raise ParamInvalidError("费用 ID 不合法") from exc
    data = await LandedCostService(session).retire_fee(
        parsed,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data)


@router.post("/calculations", response_model=ApiResponse[CalcView], summary="计算落地成本")
async def calculate(
    payload: CalcRequest,
    identity: Writer,
    _: Visible,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CalcView]:
    data = await LandedCostService(session).calculate(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        idempotency_key=idempotency_key,
    )
    return ok(data)


@router.post("/comparisons", response_model=ApiResponse[CompareView], summary="对比两种落地成本情景")
async def compare(
    payload: CompareRequest,
    identity: Writer,
    _: Visible,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CompareView]:
    data = await LandedCostService(session).compare(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        idempotency_key=idempotency_key,
    )
    return ok(data)
