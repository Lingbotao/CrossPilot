"""经营看板。读路径只查预聚合表；刷新时才扫描订单、日利润、广告和库存。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ParamInvalidError
from app.engines.ads import ratio
from app.engines.dashboard import (
    ROI_FORMULA,
    TURNOVER_FORMULA,
    CurrencyRollup,
    DayRollup,
    PlatformRollup,
    ShopDay,
    SkuRank,
    SkuStock,
    WindowSummary,
    assemble_shop_days,
    assess_inventory,
    change_ratio,
    fulfillment_rate,
    percent_text,
    previous_window,
    promo_marks,
    rank_skus,
    return_rate,
    summarize,
    turnover_days,
    year_window,
)
from app.engines.landed_cost import LINE_CODES, money_text
from app.repositories.dashboard import DashboardRepository
from app.schemas.dashboard import (
    AdsRoiView,
    CostLineView,
    DashboardOverviewView,
    DashboardRebuildView,
    DayPointView,
    FulfillmentView,
    InventoryHealthView,
    MoneyChangeView,
    PlatformCompareView,
    PromoView,
    SkuRankingView,
    SkuRankView,
    StaleAmountView,
    TrendView,
)


def _window(start: date, end: date) -> tuple[date, date]:
    if end < start or (end - start).days + 1 > settings.dashboard_max_days:
        raise ParamInvalidError("日期区间无效或超过允许的天数")
    return start, end


def _shown(value: Decimal | None, *, visible: bool) -> str | None:
    if not visible or value is None:
        return None
    return money_text(value)


def _percent(value: Decimal | None, *, visible: bool = True) -> str | None:
    if not visible:
        return None
    return percent_text(value)


def _currency(summary: WindowSummary, code: str) -> CurrencyRollup | None:
    return next((item for item in summary.currencies if item.currency == code), None)


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = DashboardRepository(session)

    async def rebuild(self, *, date_from: date, date_to: date, book_currency: str) -> DashboardRebuildView:
        start, end = _window(date_from, date_to)
        days = assemble_shop_days(
            orders=await self.repo.order_facts(start, end, sla_hours=settings.order_ship_sla_hours),
            profits=await self.repo.profit_rows(start, end),
            ads=await self.repo.ad_facts(start, end),
            returns=await self.repo.return_facts(start, end),
            shops=await self.repo.shops(),
            book_currency=book_currency,
        )
        written = await self.repo.replace_shop_days(start, end, days)
        today = datetime.now(UTC).date()
        stale_start = today - timedelta(days=settings.dashboard_stale_days - 1)
        sold = await self.repo.sold_by_sku(stale_start, today)
        stocks = [
            SkuStock(
                sku_id=item.sku_id,
                available=item.available,
                safe_stock=item.safe_stock,
                sold_qty=sold.get(item.sku_id, 0),
                purchase_price=item.purchase_price,
                currency=item.currency,
            )
            for item in await self.repo.inventory_balances()
        ]
        health = assess_inventory(stocks)
        await self.repo.replace_inventory(
            today,
            on_hand_qty=health.on_hand_qty,
            stockout_sku_count=health.stockout_sku_count,
            below_safe_sku_count=health.below_safe_sku_count,
            stale_sku_count=health.stale_sku_count,
            stale_amounts=[
                {
                    "currency": item.currency,
                    "amount": None if item.amount is None else money_text(item.amount),
                    "complete": item.complete,
                }
                for item in health.stale_amounts
            ],
        )
        return DashboardRebuildView(shop_days=written, inventory_as_of=today)

    async def overview(
        self,
        *,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
        visible: bool,
    ) -> DashboardOverviewView:
        start, end = _window(date_from, date_to)
        current = summarize(await self._rows(start, end, shop_id, platform_code))
        previous = summarize(await self._rows(*previous_window(start, end), shop_id, platform_code))
        yoy = summarize(await self._rows(*year_window(start, end), shop_id, platform_code))
        return DashboardOverviewView(
            order_count=current.order_count,
            order_change=_percent(change_ratio(current.order_count, previous.order_count)),
            order_yoy=_percent(change_ratio(current.order_count, yoy.order_count)),
            on_time_rate=_percent(fulfillment_rate(current.on_time, current.late)),
            return_rate=_percent(return_rate(current.returns, current.order_count)),
            currencies=[_money_change(item, previous, yoy, visible) for item in current.currencies],
            days=[_day_point(item, visible) for item in current.days],
            promos=[PromoView(stat_date=item.stat_date, code=item.code) for item in promo_marks(start, end)],
        )

    async def platforms(
        self,
        *,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
        visible: bool,
    ) -> list[PlatformCompareView]:
        start, end = _window(date_from, date_to)
        summary = summarize(await self._rows(start, end, shop_id, platform_code))
        return [_platform_view(item, visible) for item in summary.platforms]

    async def trends(
        self,
        *,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
        visible: bool,
    ) -> TrendView:
        start, end = _window(date_from, date_to)
        summary = summarize(await self._rows(start, end, shop_id, platform_code))
        return TrendView(
            days=[_day_point(item, visible) for item in summary.days],
            promos=[PromoView(stat_date=item.stat_date, code=item.code) for item in promo_marks(start, end)],
        )

    async def costs(
        self,
        *,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
    ) -> list[CostLineView]:
        start, end = _window(date_from, date_to)
        summary = summarize(await self._rows(start, end, shop_id, platform_code))
        views: list[CostLineView] = []
        for currency in sorted(summary.lines):
            lines = summary.lines[currency]
            whole = all(line.complete and line.amount is not None for line in lines)
            total = sum((line.amount for line in lines if line.amount is not None), Decimal("0")) if whole else None
            for code in LINE_CODES:
                line = next((item for item in lines if item.code == code), None)
                amount = None if line is None else line.amount
                views.append(
                    CostLineView(
                        code=code,
                        currency=currency,
                        amount=None if amount is None else money_text(amount),
                        share=None if total is None or amount is None else percent_text(ratio(amount, total)),
                        complete=bool(line and line.complete),
                    )
                )
        return views

    async def ranking(
        self,
        *,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
    ) -> SkuRankingView:
        start, end = _window(date_from, date_to)
        raw = await self.repo.sku_ranks(start, end, shop_id=shop_id, platform_code=platform_code)
        ranks: list[SkuRank] = []
        incomplete = 0
        for row in raw:
            complete = bool(row[9])
            net = None if row[7] is None or not complete else Decimal(row[7])
            book = None if row[8] is None or not complete else Decimal(row[8])
            revenue = Decimal(row[6])
            if not complete or net is None:
                incomplete += 1
            ranks.append(
                SkuRank(
                    sku_id=int(row[0]),
                    spu_id=int(row[1]),
                    sku_code=str(row[2]),
                    currency=str(row[3]).strip(),
                    book_currency=str(row[4]).strip(),
                    quantity=int(row[5]),
                    revenue=revenue,
                    net_profit=net,
                    book_net_profit=book,
                    net_margin=None if net is None else ratio(net, revenue),
                    loss=bool(net is not None and net < 0),
                    complete=complete and net is not None,
                )
            )
        top, bottom, loss = rank_skus(ranks, limit=settings.dashboard_rank_limit)
        return SkuRankingView(
            top=[_rank_view(item) for item in top],
            bottom=[_rank_view(item) for item in bottom],
            loss=[_rank_view(item) for item in loss],
            incomplete_count=incomplete,
        )

    async def inventory(
        self,
        *,
        date_from: date,
        date_to: date,
        visible: bool,
    ) -> InventoryHealthView:
        start, end = _window(date_from, date_to)
        row = await self.repo.latest_inventory(end)
        sold = await self.repo.sold_qty(start, end)
        window_days = (end - start).days + 1
        if row is None:
            return InventoryHealthView(
                as_of=None,
                on_hand_qty=0,
                stockout_sku_count=0,
                below_safe_sku_count=0,
                stale_sku_count=0,
                sold_qty=sold,
                turnover_days=None,
                turnover_formula=TURNOVER_FORMULA,
                stale_amounts=[],
            )
        return InventoryHealthView(
            as_of=row.stat_date,
            on_hand_qty=int(row.on_hand_qty),
            stockout_sku_count=int(row.stockout_sku_count),
            below_safe_sku_count=int(row.below_safe_sku_count),
            stale_sku_count=int(row.stale_sku_count),
            sold_qty=sold,
            turnover_days=_shown(turnover_days(int(row.on_hand_qty), sold, window_days), visible=True),
            turnover_formula=TURNOVER_FORMULA,
            stale_amounts=[
                StaleAmountView(
                    currency=str(item.get("currency") or ""),
                    amount=_shown(
                        None if item.get("amount") is None else Decimal(str(item["amount"])), visible=visible
                    ),
                    complete=bool(item.get("complete")),
                )
                for item in list(row.stale_amounts or [])
                if isinstance(item, dict)
            ],
        )

    async def ads_roi(
        self,
        *,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
        visible: bool,
    ) -> list[AdsRoiView]:
        start, end = _window(date_from, date_to)
        summary = summarize(await self._rows(start, end, shop_id, platform_code))
        return [
            AdsRoiView(
                currency=item.currency,
                ad_spend=_shown(item.ad_spend, visible=visible),
                ad_sales=_shown(item.ad_sales, visible=visible),
                acos=_percent(item.acos, visible=visible),
                roas=_shown(item.roas, visible=visible),
                profit_roi=_percent(item.profit_roi, visible=visible),
                roi_formula=ROI_FORMULA,
                ad_loss_count=item.ad_loss_count if visible else 0,
            )
            for item in summary.currencies
            if item.ad_spend is not None
        ]

    async def fulfillment(
        self,
        *,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
    ) -> list[FulfillmentView]:
        start, end = _window(date_from, date_to)
        summary = summarize(await self._rows(start, end, shop_id, platform_code))
        return [
            FulfillmentView(
                platform_code=item.platform_code,
                site_code=item.site_code,
                currency=item.currency,
                order_count=item.order_count,
                on_time=item.on_time,
                late=item.late,
                on_time_rate=_percent(fulfillment_rate(item.on_time, item.late)),
                return_count=item.return_count,
                return_rate=_percent(return_rate(item.return_count, item.order_count)),
            )
            for item in summary.platforms
        ]

    async def _rows(
        self,
        start: date,
        end: date,
        shop_id: int | None,
        platform_code: str | None,
    ) -> list[ShopDay]:
        return await self.repo.list_shop_days(start, end, shop_id=shop_id, platform_code=platform_code)


def _money_change(item: CurrencyRollup, previous: WindowSummary, yoy: WindowSummary, visible: bool) -> MoneyChangeView:
    before = _currency(previous, item.currency)
    last_year = _currency(yoy, item.currency)
    return MoneyChangeView(
        currency=item.currency,
        book_currency=item.book_currency,
        order_count=item.order_count,
        gmv=money_text(item.gmv),
        book_gmv=_shown(item.book_gmv, visible=True),
        net_profit=_shown(item.net_profit, visible=visible),
        book_net_profit=_shown(item.book_net_profit, visible=visible),
        net_margin=_percent(item.net_margin, visible=visible),
        gmv_change=_percent(change_ratio(item.gmv, None if before is None else before.gmv)),
        gmv_yoy=_percent(change_ratio(item.gmv, None if last_year is None else last_year.gmv)),
        profit_change=_percent(
            change_ratio(item.net_profit, None if before is None else before.net_profit),
            visible=visible,
        ),
        profit_yoy=_percent(
            change_ratio(item.net_profit, None if last_year is None else last_year.net_profit),
            visible=visible,
        ),
        ad_spend=_shown(item.ad_spend, visible=visible),
        ad_sales=_shown(item.ad_sales, visible=visible),
        acos=_percent(item.acos, visible=visible),
        roas=_shown(item.roas, visible=visible),
        profit_roi=_percent(item.profit_roi, visible=visible),
        roi_formula=ROI_FORMULA,
        ad_loss_count=item.ad_loss_count if visible else 0,
    )


def _day_point(item: DayRollup, visible: bool) -> DayPointView:
    return DayPointView(
        stat_date=item.stat_date,
        platform_code=item.platform_code,
        currency=item.currency,
        order_count=item.order_count,
        gmv=money_text(item.gmv),
        net_profit=_shown(item.net_profit, visible=visible),
    )


def _platform_view(item: PlatformRollup, visible: bool) -> PlatformCompareView:
    return PlatformCompareView(
        platform_code=item.platform_code,
        site_code=item.site_code,
        currency=item.currency,
        book_currency=item.book_currency,
        order_count=item.order_count,
        gmv=money_text(item.gmv),
        book_gmv=_shown(item.book_gmv, visible=True),
        net_profit=_shown(item.net_profit, visible=visible),
        on_time_rate=_percent(fulfillment_rate(item.on_time, item.late)),
        return_rate=_percent(return_rate(item.return_count, item.order_count)),
    )


def _rank_view(item: SkuRank) -> SkuRankView:
    return SkuRankView(
        sku_id=str(item.sku_id),
        spu_id=str(item.spu_id),
        sku_code=item.sku_code,
        currency=item.currency,
        book_currency=item.book_currency,
        quantity=item.quantity,
        revenue=money_text(item.revenue),
        net_profit=None if item.net_profit is None else money_text(item.net_profit),
        book_net_profit=None if item.book_net_profit is None else money_text(item.book_net_profit),
        net_margin=percent_text(item.net_margin),
        loss=item.loss,
    )
