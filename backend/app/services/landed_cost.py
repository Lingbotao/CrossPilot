"""落地成本：费用版本、计算与情景对比。费率只来自已核对的规则。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.engines.landed_cost import FeeFact, LandedCostInput, LandedCostResult, TaxFact, compute_landed_cost
from app.models.compliance import CountryTaxRule
from app.models.enums import AuditAction
from app.models.landed_cost import (
    CALC_KIND,
    COMPARE_KIND,
    FEE_ACTIVE,
    FEE_DISABLED,
    LandedCostCalc,
    LandedCostFee,
)
from app.repositories.compliance import CountryTaxRuleRepository
from app.repositories.identity import AuditLogRepository
from app.repositories.landed_cost import LandedCostCalcRepository, LandedCostFeeRepository
from app.repositories.product import SkuRepository
from app.schemas.common import money_to_str
from app.schemas.landed_cost import (
    CalcRequest,
    CalcView,
    CompareRequest,
    CompareView,
    CostLineView,
    FeeCreate,
    FeeView,
)
from app.services.compliance_windows import format_fraction, fraction_to_percent, percent_to_fraction, ranges_overlap
from app.services.tax_rule import retire_end

LIST_LIMIT = 200


def _snapshot(row: LandedCostFee) -> dict[str, str]:
    return {
        "market": row.market,
        "channel": row.channel,
        "fee_code": row.fee_code,
        "label": row.label,
        "version": str(row.version),
        "amount": format_fraction(row.amount),
        "status": row.status,
        "source": row.source,
    }


def fee_view(row: LandedCostFee) -> FeeView:
    currency = str(row.currency).strip() if row.currency else None
    return FeeView(
        id=row.id,
        market=row.market,
        channel=row.channel,
        fee_code=row.fee_code,
        label=row.label,
        charge=row.charge,
        amount=format_fraction(row.amount),
        amount_percent=fraction_to_percent(row.amount) if row.charge == "RATE" else None,
        currency=currency or None,
        volumetric_divisor=row.volumetric_divisor,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        version=row.version,
        status=row.status,
        source=row.source,
        verified_by=str(row.verified_by),
        verified_at=row.verified_at,
    )


def _volume(payload: CalcRequest) -> Decimal | None:
    if payload.volume_cm3 is not None:
        return payload.volume_cm3
    if payload.length_cm is None or payload.width_cm is None or payload.height_cm is None:
        return None
    return payload.length_cm * payload.width_cm * payload.height_cm


def _to_input(payload: CalcRequest) -> LandedCostInput:
    return LandedCostInput(
        market=payload.market,
        selling_currency=payload.selling_currency,
        channel=payload.channel,
        first_mile_method=payload.first_mile_method,
        selling_price=payload.selling_price,
        purchase_amount=payload.purchase_amount,
        purchase_currency=payload.purchase_currency,
        fx_rate=payload.fx_rate,
        fx_source=payload.fx_source,
        weight_g=payload.weight_g,
        volume_cm3=_volume(payload),
        hs_code=payload.hs_code,
        declared_value=payload.declared_value,
        declared_currency=payload.declared_currency,
        shipment_cost=payload.shipment_cost,
        shipment_currency=payload.shipment_currency,
        shipment_weight_g=payload.shipment_weight_g,
        shipment_volume_cm3=payload.shipment_volume_cm3,
        shipment_value=payload.shipment_value,
        storage_days=payload.storage_days,
    )


def _latest_tax(rows: list[CountryTaxRule]) -> list[CountryTaxRule]:
    best: dict[tuple[str, str], CountryTaxRule] = {}
    for row in rows:
        key = (row.tax_type, row.hs_code_pattern)
        current = best.get(key)
        if current is None or row.version > current.version:
            best[key] = row
    return list(best.values())


def _latest_fee(rows: list[LandedCostFee]) -> list[LandedCostFee]:
    best: dict[tuple[str, str, str], LandedCostFee] = {}
    for row in rows:
        key = (row.fee_code, row.channel, row.label)
        current = best.get(key)
        if current is None or row.version > current.version:
            best[key] = row
    return list(best.values())


def _tax_fact(row: CountryTaxRule) -> TaxFact:
    currency = str(row.threshold_currency).strip() if row.threshold_currency else None
    return TaxFact(
        tax_type=row.tax_type,
        hs_code_pattern=row.hs_code_pattern,
        rate=row.rate,
        basis_numerator=row.basis_numerator,
        basis_denominator=row.basis_denominator,
        threshold_amount=row.threshold_amount,
        threshold_currency=currency or None,
        source=row.source,
        verified_at=row.verified_at.date(),
    )


def _fee_fact(row: LandedCostFee) -> FeeFact:
    currency = str(row.currency).strip() if row.currency else None
    return FeeFact(
        fee_code=row.fee_code,
        channel=row.channel,
        label=row.label,
        charge=row.charge,
        amount=row.amount,
        currency=currency or None,
        volumetric_divisor=row.volumetric_divisor,
        source=row.source,
        verified_at=row.verified_at.date(),
    )


def _calc_view(calc_id: int, name: str, market: str, result: LandedCostResult) -> CalcView:
    return CalcView(
        id=calc_id,
        name=name,
        market=market,
        currency=result.currency,
        lines=[
            CostLineView(
                code=line.code,
                label=line.label,
                amount=money_to_str(line.amount),
                currency=line.currency,
                formula=line.formula,
                source=line.source,
                complete=line.complete,
            )
            for line in result.lines
        ],
        landed_cost=money_to_str(result.landed_cost),
        net_profit=money_to_str(result.net_profit),
        net_margin_percent=money_to_str(result.net_margin_percent),
        roi_percent=money_to_str(result.roi_percent),
        complete=result.complete,
        profit_complete=result.profit_complete,
    )


class LandedCostService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.fees = LandedCostFeeRepository(session)
        self.calcs = LandedCostCalcRepository(session)
        self.taxes = CountryTaxRuleRepository(session)
        self.skus = SkuRepository(session)
        self.audit = AuditLogRepository(session)

    async def list_fees(self, *, market: str | None, limit: int) -> list[FeeView]:
        rows = await self.fees.list_visible(market=market, limit=min(limit, LIST_LIMIT))
        return [fee_view(row) for row in rows]

    async def create_fee(self, payload: FeeCreate, *, tenant_id: int, actor_id: int) -> FeeView:
        existing = await self.fees.list_for_key(payload.market, payload.channel, payload.fee_code, payload.label)
        active = [row for row in existing if row.status == FEE_ACTIVE]
        ends = {row.id: row.effective_to for row in active}
        truncate: LandedCostFee | None = None
        opens = [row for row in active if row.effective_to is None]
        if len(opens) == 1 and opens[0].effective_from < payload.effective_from:
            current = opens[0]
            if ranges_overlap(current.effective_from, None, payload.effective_from, payload.effective_to):
                ends[current.id] = payload.effective_from
                truncate = current
        for row in active:
            if ranges_overlap(row.effective_from, ends[row.id], payload.effective_from, payload.effective_to):
                raise AppError("与已有费用版本的生效区间重叠", code=ErrorCode.FEE_RULE_OVERLAP)
        before: dict[str, str] | None = None
        if truncate is not None:
            before = _snapshot(truncate)
            truncate.effective_to = payload.effective_from
            truncate.updated_by = actor_id
            self.fees.assert_tenant_owned(truncate)
        amount = (
            percent_to_fraction(payload.amount)
            if payload.charge == "RATE"
            else payload.amount.quantize(Decimal("0.000001"))
        )
        now = datetime.now(UTC)
        row = LandedCostFee(
            tenant_id=tenant_id,
            market=payload.market,
            channel=payload.channel,
            fee_code=payload.fee_code,
            label=payload.label,
            charge=payload.charge,
            amount=amount,
            currency=payload.currency,
            volumetric_divisor=payload.volumetric_divisor,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            version=max((item.version for item in existing), default=0) + 1,
            status=FEE_ACTIVE,
            source=payload.source,
            verified_by=actor_id,
            verified_at=now,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.fees.add(row)
        await self.session.refresh(row)
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.LANDED_COST_FEE,
            resource="landed_cost_fee",
            resource_id=row.id,
            before=before,
            after=_snapshot(row),
        )
        return fee_view(row)

    async def retire_fee(self, fee_id: int, *, tenant_id: int, actor_id: int, today: date | None = None) -> FeeView:
        row = await self.fees.get_or_404(fee_id)
        if row.status == FEE_DISABLED:
            return fee_view(row)
        today = today or datetime.now(UTC).date()
        before = _snapshot(row)
        row.status = FEE_DISABLED
        row.effective_to = retire_end(row.effective_from, row.effective_to, today)
        row.updated_by = actor_id
        self.fees.assert_tenant_owned(row)
        await self.session.flush()
        await self.session.refresh(row)
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.LANDED_COST_FEE,
            resource="landed_cost_fee",
            resource_id=row.id,
            before=before,
            after=_snapshot(row),
        )
        return fee_view(row)

    async def calculate(
        self,
        payload: CalcRequest,
        *,
        tenant_id: int,
        actor_id: int,
        idempotency_key: str | None,
    ) -> CalcView:
        key = _clean_key(idempotency_key)
        if key:
            existing = await self.calcs.get_by_key(key)
            if existing is not None:
                if existing.kind != CALC_KIND:
                    raise AppError("幂等键已被另一次计算使用", code=ErrorCode.IDEMPOTENCY_CONFLICT)
                return CalcView.model_validate(existing.result)
        await self._ensure_sku(payload.sku_id)
        on = payload.as_of or datetime.now(UTC).date()
        taxes, fees = await self._facts(payload.market, on)
        result = compute_landed_cost(_to_input(payload), taxes, fees)
        row = await self._store(
            kind=CALC_KIND,
            market=payload.market,
            sku_id=payload.sku_id,
            key=key,
            params=payload.model_dump(mode="json"),
            actor_id=actor_id,
            tenant_id=tenant_id,
            complete=result.complete,
        )
        view = _calc_view(row.id, payload.name, payload.market, result)
        row.result = view.model_dump(mode="json")
        await self.session.flush()
        return view

    async def compare(
        self,
        payload: CompareRequest,
        *,
        tenant_id: int,
        actor_id: int,
        idempotency_key: str | None,
    ) -> CompareView:
        key = _clean_key(idempotency_key)
        if key:
            existing = await self.calcs.get_by_key(key)
            if existing is not None:
                if existing.kind != COMPARE_KIND:
                    raise AppError("幂等键已被另一次计算使用", code=ErrorCode.IDEMPOTENCY_CONFLICT)
                return CompareView.model_validate(existing.result)
        await self._ensure_sku(payload.left.sku_id)
        await self._ensure_sku(payload.right.sku_id)
        left = await self._compute(payload.left)
        right = await self._compute(payload.right)
        row = await self._store(
            kind=COMPARE_KIND,
            market=payload.left.market,
            sku_id=payload.left.sku_id,
            key=key,
            params=payload.model_dump(mode="json"),
            actor_id=actor_id,
            tenant_id=tenant_id,
            complete=left.complete and right.complete,
        )
        view = CompareView(
            id=row.id,
            left=_calc_view(row.id, payload.left.name or "方案 A", payload.left.market, left),
            right=_calc_view(row.id, payload.right.name or "方案 B", payload.right.market, right),
        )
        row.result = view.model_dump(mode="json")
        await self.session.flush()
        return view

    async def _compute(self, payload: CalcRequest) -> LandedCostResult:
        on = payload.as_of or datetime.now(UTC).date()
        taxes, fees = await self._facts(payload.market, on)
        return compute_landed_cost(_to_input(payload), taxes, fees)

    async def _facts(self, market: str, on: date) -> tuple[list[TaxFact], list[FeeFact]]:
        taxes = [_tax_fact(row) for row in _latest_tax(await self.taxes.list_effective(market, on))]
        fees = [_fee_fact(row) for row in _latest_fee(await self.fees.list_effective(market, on))]
        return taxes, fees

    async def _ensure_sku(self, sku_id: int | None) -> None:
        if sku_id is not None:
            await self.skus.get_or_404(sku_id)

    async def _store(
        self,
        *,
        kind: str,
        market: str,
        sku_id: int | None,
        key: str | None,
        params: dict[str, object],
        actor_id: int,
        tenant_id: int,
        complete: bool,
    ) -> LandedCostCalc:
        row = LandedCostCalc(
            tenant_id=tenant_id,
            sku_id=sku_id,
            market=market,
            kind=kind,
            idempotency_key=key,
            params=params,
            result={},
            complete=complete,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.calcs.add(row)
        await self.session.refresh(row)
        return row


def _clean_key(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None
