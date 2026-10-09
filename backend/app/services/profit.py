"""汇率维护与 SKU 日利润物化。缺汇率留空，不编造。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, ExchangeRateLockedError, ExchangeRateOverlapError
from app.engines.landed_cost import (
    LINE_CODES,
    CostLine,
    FeeFact,
    LandedCostInput,
    TaxFact,
    compute_landed_cost,
    quantize,
)
from app.engines.profit import DailyRollup, build_waterfall, fx_gain_loss, period_bounds, rollup_daily
from app.models.enums import AuditAction
from app.models.finance import BOOK_BASIS, ORDER_BASIS, SETTLEMENT_BASIS, ExchangeRate, SkuProfitDaily
from app.repositories.finance import ExchangeRateRepository, ProfitSourceLine, SkuCostFact, SkuProfitRepository
from app.repositories.identity import AuditLogRepository
from app.schemas.common import money_to_str
from app.schemas.profit import (
    MaterializeRequest,
    MaterializeView,
    ProfitRowView,
    RateCreate,
    RateView,
    WaterfallStepView,
    WaterfallView,
)
from app.services.compliance_windows import format_fraction
from app.services.landed_cost import LandedCostService

_HUNDRED = Decimal("100")


def rate_view(row: ExchangeRate) -> RateView:
    return RateView(
        id=row.id,
        base_currency=str(row.base_currency).strip(),
        quote_currency=str(row.quote_currency).strip(),
        rate=format_fraction(row.rate),
        basis=row.basis,
        effective_on=row.effective_on,
        source=row.source,
        locked=row.locked,
    )


class ProfitService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.rates = ExchangeRateRepository(session)
        self.daily = SkuProfitRepository(session)
        self.audit = AuditLogRepository(session)
        self.costs = LandedCostService(session)

    async def list_rates(
        self,
        *,
        basis: str | None,
        base_currency: str | None,
        quote_currency: str | None,
        limit: int,
    ) -> list[RateView]:
        rows = await self.rates.list_visible(
            basis=basis,
            base_currency=base_currency,
            quote_currency=quote_currency,
            limit=min(limit, 200),
        )
        return [rate_view(row) for row in rows]

    async def create_rate(
        self,
        payload: RateCreate,
        *,
        tenant_id: int,
        actor_id: int,
        idempotency_key: str | None,
    ) -> RateView:
        key = _clean_key(idempotency_key)
        if key:
            existing = await self.rates.get_by_key(key)
            if existing is not None:
                return rate_view(existing)
        current = await self.rates.get_exact(
            payload.base_currency,
            payload.quote_currency,
            payload.basis,
            payload.effective_on,
        )
        if current is not None:
            if current.locked:
                raise ExchangeRateLockedError()
            raise ExchangeRateOverlapError()
        row = ExchangeRate(
            tenant_id=tenant_id,
            base_currency=payload.base_currency,
            quote_currency=payload.quote_currency,
            rate=payload.rate.quantize(Decimal("0.000001")),
            basis=payload.basis,
            effective_on=payload.effective_on,
            source=payload.source,
            locked=False,
            idempotency_key=key,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.rates.add(row)
        await self.session.refresh(row)
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.EXCHANGE_RATE,
            resource="exchange_rate",
            resource_id=row.id,
            before=None,
            after=_rate_snapshot(row),
        )
        return rate_view(row)

    async def lock_rate(self, rate_id: int, *, tenant_id: int, actor_id: int) -> RateView:
        row = await self.rates.get_or_404(rate_id)
        if row.locked:
            return rate_view(row)
        before = _rate_snapshot(row)
        row.locked = True
        row.updated_by = actor_id
        self.rates.assert_tenant_owned(row)
        await self.session.flush()
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.EXCHANGE_RATE,
            resource="exchange_rate",
            resource_id=row.id,
            before=before,
            after=_rate_snapshot(row),
        )
        return rate_view(row)

    async def materialize(
        self,
        payload: MaterializeRequest,
        *,
        tenant_id: int,
        actor_id: int,
    ) -> MaterializeView:
        lines = await self.daily.order_lines(payload.date_from, payload.date_to)
        unmatched = 0
        groups: dict[tuple[int, int, date, str], list[ProfitSourceLine]] = defaultdict(list)
        for line in lines:
            if line.sku_id is None:
                unmatched += 1
                continue
            groups[(line.sku_id, line.shop_id, line.stat_date, line.currency)].append(line)
        sku_ids = [sku_id for sku_id, _, _, _ in groups]
        facts = await self.daily.sku_costs(sku_ids)
        hs = await self.daily.hs_codes(list({fact.spu_id for fact in facts.values()}), payload.market)
        rules: dict[date, tuple[list[TaxFact], list[FeeFact], frozenset[str]]] = {}
        rate_cache: dict[tuple[str, str, str, date], ExchangeRate | None] = {}
        written = 0
        incomplete = 0
        for (sku_id, shop_id, stat_date, currency), bucket in groups.items():
            if stat_date not in rules:
                rules[stat_date] = await self.costs.load_rules(payload.market, payload.channel, stat_date)
            taxes, fees, disabled = rules[stat_date]
            book_rate, _book_source = await self._rate(
                rate_cache,
                currency,
                payload.book_currency,
                BOOK_BASIS,
                stat_date,
            )
            order_rate, order_source = await self._rate(
                rate_cache,
                currency,
                payload.book_currency,
                ORDER_BASIS,
                stat_date,
            )
            settlement_rate, settlement_source = await self._rate(
                rate_cache,
                currency,
                payload.book_currency,
                SETTLEMENT_BASIS,
                stat_date,
            )
            units = []
            quantity = 0
            revenue = Decimal("0")
            for line in bucket:
                quantity += line.quantity
                revenue += quantize(line.unit_price * Decimal(line.quantity))
                fact = facts.get(line.sku_id or 0)
                purchase_currency = None if fact is None else fact.purchase_currency
                purchase_rate, purchase_source = await self._purchase_rate(
                    rate_cache,
                    currency,
                    purchase_currency,
                    payload.book_currency,
                    stat_date,
                    order_rate,
                    order_source,
                )
                units.append(
                    (
                        line.quantity,
                        line.unit_price,
                        compute_landed_cost(
                            _input(payload, line, fact, hs, purchase_rate, purchase_source),
                            taxes,
                            fees,
                            disabled=disabled,
                        ),
                    )
                )
            same = currency == payload.book_currency
            fx = fx_gain_loss(
                foreign_amount=quantize(revenue),
                order_rate=None if same else (None if order_rate is None else order_rate.rate),
                settlement_rate=None if same else (None if settlement_rate is None else settlement_rate.rate),
                order_source=order_source,
                settlement_source=settlement_source,
                book_currency=payload.book_currency,
                same_currency=same,
            )
            rolled = rollup_daily(
                units,
                book_rate=Decimal("1") if same else (None if book_rate is None else book_rate.rate),
                book_currency=payload.book_currency,
                fx=fx,
            )
            await self._save_daily(
                sku_id=sku_id,
                shop_id=shop_id,
                stat_date=stat_date,
                currency=currency,
                book_currency=payload.book_currency,
                quantity=quantity,
                rolled=rolled,
                actor_id=actor_id,
                tenant_id=tenant_id,
            )
            written += 1
            if not rolled.complete:
                incomplete += 1
        return MaterializeView(rows=written, unmatched_items=unmatched, incomplete_rows=incomplete)

    async def list_profit(
        self,
        *,
        grain: str,
        date_from: date,
        date_to: date,
        sku_id: int | None,
        shop_id: int | None,
        currency: str | None,
    ) -> list[ProfitRowView]:
        rows = await self.daily.list_between(
            date_from=date_from,
            date_to=date_to,
            sku_id=sku_id,
            shop_id=shop_id,
            currency=currency,
        )
        grouped: dict[tuple[int, int, str, date, date], list[SkuProfitDaily]] = defaultdict(list)
        for row in rows:
            start, end = period_bounds(row.stat_date, grain)
            grouped[(row.sku_id, row.shop_id, str(row.currency).strip(), start, end)].append(row)
        views: list[ProfitRowView] = []
        for (sku_id, shop_id, row_currency, start, end), bucket in grouped.items():
            views.append(_row_view(sku_id, shop_id, row_currency, start, end, bucket))
        return views

    async def waterfall(
        self,
        *,
        date_from: date,
        date_to: date,
        sku_id: int | None,
        shop_id: int | None,
        currency: str | None,
    ) -> WaterfallView:
        rows = await self.daily.list_between(
            date_from=date_from,
            date_to=date_to,
            sku_id=sku_id,
            shop_id=shop_id,
            currency=currency,
        )
        if not rows:
            chosen = currency or ""
            return WaterfallView(currency=chosen, book_currency="", steps=[])
        currencies = {str(row.currency).strip() for row in rows}
        if len(currencies) != 1:
            raise AppError("区间内有多种订单币种，请按币种筛选", code=ErrorCode.PARAM_INVALID)
        books = {str(row.book_currency).strip() for row in rows}
        book_currency = next(iter(books)) if len(books) == 1 else ""
        revenue = quantize_sum(row.revenue for row in rows)
        lines = _sum_lines(rows)
        net = None if any(row.net_profit is None for row in rows) else quantize_sum(row.net_profit for row in rows)
        fx_amount = None if any(row.fx_gain is None for row in rows) else quantize_sum(row.fx_gain for row in rows)
        fx_formula = rows[0].fx_formula if len(rows) == 1 else "区间内汇兑损益为各日合计，不计入净利。"
        steps = build_waterfall(revenue=revenue, lines=lines, net_profit=net, fx_gain=fx_amount, fx_formula=fx_formula)
        return WaterfallView(
            currency=next(iter(currencies)),
            book_currency=book_currency,
            steps=[
                WaterfallStepView(
                    code=step.code,
                    label=step.label,
                    amount=money_to_str(step.amount),
                    running=money_to_str(step.running),
                    memo=step.memo,
                    formula=step.formula,
                )
                for step in steps
            ],
        )

    async def _rate(
        self,
        cache: dict[tuple[str, str, str, date], ExchangeRate | None],
        base: str,
        quote: str,
        basis: str,
        on: date,
    ) -> tuple[ExchangeRate | None, str]:
        if base == quote:
            return None, "币种相同"
        key = (base, quote, basis, on)
        if key not in cache:
            cache[key] = await self.rates.latest(base, quote, basis, on)
        row = cache[key]
        if row is None:
            return None, "未配置"
        return row, row.source

    async def _purchase_rate(
        self,
        cache: dict[tuple[str, str, str, date], ExchangeRate | None],
        selling: str,
        purchase_currency: str | None,
        book_currency: str,
        on: date,
        order_book: ExchangeRate | None,
        order_source: str,
    ) -> tuple[Decimal | None, str]:
        if purchase_currency is None or purchase_currency == selling:
            return None, ""
        if purchase_currency == book_currency and order_book is not None:
            return order_book.rate, order_source
        row, source = await self._rate(cache, selling, purchase_currency, ORDER_BASIS, on)
        if row is None:
            return None, source
        return row.rate, source

    async def _save_daily(
        self,
        *,
        sku_id: int,
        shop_id: int,
        stat_date: date,
        currency: str,
        book_currency: str,
        quantity: int,
        rolled: DailyRollup,
        actor_id: int,
        tenant_id: int,
    ) -> None:
        row = await self.daily.get_key(sku_id, shop_id, stat_date, currency)
        payload = {
            "book_currency": book_currency,
            "revenue": rolled.revenue,
            "cost_total": rolled.cost_total,
            "net_profit": rolled.net_profit,
            "net_margin": rolled.net_margin_percent,
            "book_revenue": rolled.book_revenue,
            "book_cost_total": rolled.book_cost_total,
            "book_net_profit": rolled.book_net_profit,
            "fx_gain": rolled.fx_gain,
            "quantity": quantity,
            "lines": [_line_json(line) for line in rolled.lines],
            "fx_formula": rolled.fx_formula,
            "fx_source": rolled.fx_source,
            "complete": rolled.complete,
            "updated_by": actor_id,
        }
        if row is None:
            created = SkuProfitDaily(
                tenant_id=tenant_id,
                sku_id=sku_id,
                shop_id=shop_id,
                stat_date=stat_date,
                currency=currency,
                created_by=actor_id,
                **payload,
            )
            await self.daily.add(created)
            return
        for name, value in payload.items():
            setattr(row, name, value)
        self.daily.assert_tenant_owned(row)
        await self.session.flush()


def quantize_sum(values: Iterable[Decimal | None]) -> Decimal:
    total = Decimal("0")
    for value in values:
        if value is None:
            continue
        total += Decimal(value)
    return total.quantize(Decimal("0.000001"))


def _sum_lines(rows: list[SkuProfitDaily]) -> list[CostLine]:
    combined: list[CostLine] = []
    for code in LINE_CODES:
        amounts: list[Decimal] = []
        complete = True
        label = code
        currency = ""
        formula = ""
        source = ""
        for row in rows:
            stored = next((item for item in row.lines if item.get("code") == code), None)
            if stored is None or not stored.get("complete") or stored.get("amount") is None:
                complete = False
                continue
            label = str(stored.get("label") or code)
            currency = str(stored.get("currency") or "")
            formula = str(stored.get("formula") or "")
            source = str(stored.get("source") or "")
            amounts.append(Decimal(str(stored["amount"])))
        if not complete:
            combined.append(CostLine(code, label, None, currency, "这一项在区间内不完整。", source or "未配置", False))
            continue
        total = quantize_sum(amounts)
        combined.append(CostLine(code, label, total, currency, formula or f"{label}合计。", source or "日利润", True))
    return combined


def _row_view(
    sku_id: int,
    shop_id: int,
    currency: str,
    start: date,
    end: date,
    bucket: list[SkuProfitDaily],
) -> ProfitRowView:
    revenue = quantize_sum(row.revenue for row in bucket)
    cost = None if any(row.cost_total is None for row in bucket) else quantize_sum(row.cost_total for row in bucket)
    net = None if any(row.net_profit is None for row in bucket) else quantize_sum(row.net_profit for row in bucket)
    margin = None
    if net is not None and revenue > 0:
        margin = (net / revenue * _HUNDRED).quantize(Decimal("0.000001"))
    book_revenue = (
        None if any(row.book_revenue is None for row in bucket) else quantize_sum(row.book_revenue for row in bucket)
    )
    book_net = (
        None
        if any(row.book_net_profit is None for row in bucket)
        else quantize_sum(row.book_net_profit for row in bucket)
    )
    fx_gain = None if any(row.fx_gain is None for row in bucket) else quantize_sum(row.fx_gain for row in bucket)
    return ProfitRowView(
        sku_id=str(sku_id),
        shop_id=str(shop_id),
        period_start=start,
        period_end=end,
        currency=currency,
        book_currency=str(bucket[0].book_currency).strip(),
        revenue=money_to_str(revenue) or "0.000000",
        cost_total=money_to_str(cost),
        net_profit=money_to_str(net),
        net_margin_percent=money_to_str(margin),
        book_revenue=money_to_str(book_revenue),
        book_net_profit=money_to_str(book_net),
        fx_gain=money_to_str(fx_gain),
        quantity=sum(row.quantity for row in bucket),
        complete=all(row.complete for row in bucket),
    )


def _input(
    payload: MaterializeRequest,
    line: ProfitSourceLine,
    fact: SkuCostFact | None,
    hs: dict[int, str],
    purchase_rate: Decimal | None,
    purchase_source: str,
) -> LandedCostInput:
    volume = None
    if fact is not None:
        volume = fact.length_cm * fact.width_cm * fact.height_cm
    return LandedCostInput(
        market=payload.market,
        selling_currency=line.currency,
        channel=payload.channel,
        first_mile_method=payload.first_mile_method,
        selling_price=line.unit_price,
        purchase_amount=None if fact is None else fact.purchase_price,
        purchase_currency=None if fact is None else fact.purchase_currency,
        fx_rate=purchase_rate,
        fx_source=purchase_source,
        weight_g=None if fact is None else fact.weight_g,
        volume_cm3=volume,
        hs_code="" if fact is None else hs.get(fact.spu_id, ""),
        declared_value=line.unit_price,
        declared_currency=line.currency,
        shipment_cost=payload.shipment_cost,
        shipment_currency=payload.shipment_currency,
        shipment_weight_g=payload.shipment_weight_g,
        shipment_volume_cm3=payload.shipment_volume_cm3,
        shipment_value=payload.shipment_value,
        storage_days=payload.storage_days,
    )


def _line_json(line: CostLine) -> dict[str, str | bool | None]:
    return {
        "code": line.code,
        "label": line.label,
        "amount": money_to_str(line.amount),
        "currency": line.currency,
        "formula": line.formula,
        "source": line.source,
        "complete": line.complete,
    }


def _rate_snapshot(row: ExchangeRate) -> dict[str, str]:
    return {
        "base_currency": str(row.base_currency).strip(),
        "quote_currency": str(row.quote_currency).strip(),
        "basis": row.basis,
        "effective_on": row.effective_on.isoformat(),
        "rate": format_fraction(row.rate),
        "locked": str(row.locked).lower(),
        "source": row.source,
    }


def _clean_key(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None
