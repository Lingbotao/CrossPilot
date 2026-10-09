"""汇率、SKU 利润与瀑布。读和写都要求成本可见。"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbSession, Identity, require_cost_visibility, require_permission
from app.core.errors import ParamInvalidError
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.compliance import clean_currency
from app.schemas.listing import parse_id, parse_optional_id
from app.schemas.profit import (
    MaterializeRequest,
    MaterializeView,
    ProfitRowView,
    RateCreate,
    RateView,
    WaterfallView,
    clean_basis,
    clean_grain,
)
from app.services.profit import ProfitService

router = APIRouter(tags=["利润"])

Reader = Annotated[Identity, Depends(require_permission(Perm.FINANCE_READ))]
Writer = Annotated[Identity, Depends(require_permission(Perm.FINANCE_WRITE))]
Visible = Annotated[Identity, Depends(require_cost_visibility)]


def _optional_currency(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    try:
        return clean_currency(raw)
    except ValueError as exc:
        raise ParamInvalidError("币种不合法") from exc


def _optional_basis(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    try:
        return clean_basis(raw)
    except ValueError as exc:
        raise ParamInvalidError("汇率口径不合法") from exc


@router.get("/exchange-rates", response_model=ApiResponse[list[RateView]], summary="汇率列表")
async def list_rates(
    identity: Reader,
    _: Visible,
    session: DbSession,
    basis: Annotated[str | None, Query()] = None,
    base_currency: Annotated[str | None, Query()] = None,
    quote_currency: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ApiResponse[list[RateView]]:
    del identity
    data = await ProfitService(session).list_rates(
        basis=_optional_basis(basis),
        base_currency=_optional_currency(base_currency),
        quote_currency=_optional_currency(quote_currency),
        limit=limit,
    )
    return ok(data)


@router.post("/exchange-rates", response_model=ApiResponse[RateView], summary="登记汇率")
async def create_rate(
    payload: RateCreate,
    identity: Writer,
    _: Visible,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[RateView]:
    data = await ProfitService(session).create_rate(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
        idempotency_key=idempotency_key,
    )
    return ok(data)


@router.post("/exchange-rates/{rate_id}/lock", response_model=ApiResponse[RateView], summary="锁定汇率")
async def lock_rate(
    rate_id: str,
    identity: Writer,
    _: Visible,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[RateView]:
    try:
        parsed = parse_id(rate_id)
    except ValueError as exc:
        raise ParamInvalidError("汇率 ID 不合法") from exc
    data = await ProfitService(session).lock_rate(parsed, tenant_id=identity.tenant.id, actor_id=identity.user.id)
    return ok(data)


@router.post("/profit/materialize", response_model=ApiResponse[MaterializeView], summary="重算 SKU 日利润")
async def materialize(
    payload: MaterializeRequest,
    identity: Writer,
    _: Visible,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[MaterializeView]:
    data = await ProfitService(session).materialize(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data)


@router.get("/profit/sku", response_model=ApiResponse[list[ProfitRowView]], summary="SKU 利润")
async def list_profit(
    identity: Reader,
    _: Visible,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    grain: Annotated[str, Query()] = "day",
    sku_id: Annotated[str | None, Query()] = None,
    shop_id: Annotated[str | None, Query()] = None,
    currency: Annotated[str | None, Query()] = None,
) -> ApiResponse[list[ProfitRowView]]:
    del identity
    try:
        chosen = clean_grain(grain)
        parsed_sku = parse_optional_id(sku_id)
        parsed_shop = parse_optional_id(shop_id)
    except ValueError as exc:
        raise ParamInvalidError("利润查询参数不合法") from exc
    if date_to < date_from:
        raise ParamInvalidError("结束日不能早于开始日")
    data = await ProfitService(session).list_profit(
        grain=chosen,
        date_from=date_from,
        date_to=date_to,
        sku_id=parsed_sku,
        shop_id=parsed_shop,
        currency=_optional_currency(currency),
    )
    return ok(data)


@router.get("/profit/waterfall", response_model=ApiResponse[WaterfallView], summary="利润瀑布")
async def waterfall(
    identity: Reader,
    _: Visible,
    session: DbSession,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    sku_id: Annotated[str | None, Query()] = None,
    shop_id: Annotated[str | None, Query()] = None,
    currency: Annotated[str | None, Query()] = None,
) -> ApiResponse[WaterfallView]:
    del identity
    try:
        parsed_sku = parse_optional_id(sku_id)
        parsed_shop = parse_optional_id(shop_id)
    except ValueError as exc:
        raise ParamInvalidError("瀑布查询参数不合法") from exc
    if date_to < date_from:
        raise ParamInvalidError("结束日不能早于开始日")
    data = await ProfitService(session).waterfall(
        date_from=date_from,
        date_to=date_to,
        sku_id=parsed_sku,
        shop_id=parsed_shop,
        currency=_optional_currency(currency),
    )
    return ok(data)
