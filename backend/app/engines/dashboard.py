"""经营看板汇总。纯函数，不访问数据库，也不做汇率换算。

GMV 取已付款订单的原币金额。净利直接用 SKU 日利润，不另造口径。
本位币金额只用日利润里已经算好的 book_*；缺任何一天就不汇总，避免把不完整利润当成结论。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.engines.ads import as_percent, ratio
from app.engines.landed_cost import LINE_CODES, money_text, quantize

GMV_STATUSES: tuple[str, ...] = (
    "PAID",
    "SHIPPED",
    "DELIVERED",
    "COMPLETED",
    "REFUNDING",
    "REFUNDED",
    "RETURNED",
)
ROI_FORMULA = "净利 / (采购成本 + 头程物流分摊)"
TURNOVER_FORMULA = "在手数量 × 天数 / 区间销量"
PROMO_CODES: tuple[str, ...] = (
    "PROMO_618",
    "PROMO_99",
    "PROMO_1111",
    "PROMO_1212",
    "BLACK_FRIDAY",
    "CYBER_MONDAY",
)
_FIXED_PROMOS: tuple[tuple[int, int, str], ...] = (
    (6, 18, "PROMO_618"),
    (9, 9, "PROMO_99"),
    (11, 11, "PROMO_1111"),
    (12, 12, "PROMO_1212"),
)


@dataclass(frozen=True)
class PromoMark:
    stat_date: date
    code: str


@dataclass(frozen=True)
class CostLineTotal:
    code: str
    amount: Decimal | None
    complete: bool


@dataclass(frozen=True)
class ProfitRow:
    shop_id: int
    stat_date: date
    currency: str
    book_currency: str
    revenue: Decimal
    book_revenue: Decimal | None
    net_profit: Decimal | None
    book_net_profit: Decimal | None
    complete: bool
    quantity: int
    lines: list[dict[str, object]]


@dataclass(frozen=True)
class OrderFact:
    shop_id: int
    platform_code: str
    site_code: str
    stat_date: date
    currency: str
    order_count: int
    gmv: Decimal
    on_time: int
    late: int


@dataclass(frozen=True)
class AdFact:
    shop_id: int
    stat_date: date
    currency: str
    spend: Decimal
    sales: Decimal
    loss_count: int


@dataclass(frozen=True)
class ReturnFact:
    shop_id: int
    stat_date: date
    currency: str
    count: int


@dataclass(frozen=True)
class ShopDay:
    shop_id: int
    platform_code: str
    site_code: str
    stat_date: date
    currency: str
    book_currency: str
    order_count: int
    gmv: Decimal
    book_gmv: Decimal | None
    net_profit: Decimal | None
    book_net_profit: Decimal | None
    profit_complete: bool
    on_time: int
    late: int
    return_count: int
    ad_spend: Decimal | None
    ad_sales: Decimal | None
    ad_loss_count: int
    lines: list[CostLineTotal] = field(default_factory=list)


@dataclass(frozen=True)
class CurrencyRollup:
    currency: str
    book_currency: str
    order_count: int
    gmv: Decimal
    book_gmv: Decimal | None
    net_profit: Decimal | None
    book_net_profit: Decimal | None
    profit_complete: bool
    ad_spend: Decimal | None
    ad_sales: Decimal | None
    ad_loss_count: int
    net_margin: Decimal | None
    acos: Decimal | None
    roas: Decimal | None
    profit_roi: Decimal | None


@dataclass(frozen=True)
class PlatformRollup:
    platform_code: str
    site_code: str
    currency: str
    book_currency: str
    order_count: int
    gmv: Decimal
    book_gmv: Decimal | None
    net_profit: Decimal | None
    profit_complete: bool
    on_time: int
    late: int
    return_count: int


@dataclass(frozen=True)
class DayRollup:
    stat_date: date
    platform_code: str
    currency: str
    order_count: int
    gmv: Decimal
    net_profit: Decimal | None
    profit_complete: bool


@dataclass(frozen=True)
class WindowSummary:
    order_count: int
    on_time: int
    late: int
    returns: int
    currencies: list[CurrencyRollup]
    platforms: list[PlatformRollup]
    days: list[DayRollup]
    lines: dict[str, list[CostLineTotal]]


@dataclass(frozen=True)
class SkuStock:
    sku_id: int
    available: int
    safe_stock: int
    sold_qty: int
    purchase_price: Decimal | None
    currency: str | None


@dataclass(frozen=True)
class StaleAmount:
    currency: str
    amount: Decimal | None
    complete: bool


@dataclass(frozen=True)
class InventoryHealth:
    on_hand_qty: int
    stockout_sku_count: int
    below_safe_sku_count: int
    stale_sku_count: int
    stale_amounts: list[StaleAmount]


@dataclass(frozen=True)
class SkuRank:
    sku_id: int
    spu_id: int
    sku_code: str
    currency: str
    book_currency: str
    quantity: int
    revenue: Decimal
    net_profit: Decimal | None
    book_net_profit: Decimal | None
    net_margin: Decimal | None
    loss: bool
    complete: bool


def shift_year(day: date) -> date:
    """去年同一天。闰日没有对应日时落到 2 月 28 日。"""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return date(day.year - 1, 2, 28)


def previous_window(start: date, end: date) -> tuple[date, date]:
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    return prev_end - timedelta(days=days - 1), prev_end


def year_window(start: date, end: date) -> tuple[date, date]:
    return shift_year(start), shift_year(end)


def change_ratio(current: Decimal | int | None, previous: Decimal | int | None) -> Decimal | None:
    """(本期 - 上期) / 上期。上期为 0 或缺失时没有环比。"""
    if current is None or previous is None:
        return None
    return ratio(Decimal(current) - Decimal(previous), previous)


def shipment_timing(
    *,
    status: str,
    shipped_at: datetime | None,
    deadline: datetime,
    now: datetime,
) -> str:
    """及时、超时，或仍在 SLA 内未发货。未付款和已取消不进入履约分母。"""
    if status not in GMV_STATUSES:
        return "ignore"
    if shipped_at is not None and shipped_at <= deadline:
        return "on_time"
    if shipped_at is not None and shipped_at > deadline:
        return "late"
    if shipped_at is None and status == "PAID" and deadline < now:
        return "late"
    return "open"


def black_friday(year: int) -> date:
    """感恩节后的星期五。感恩节是 11 月第四个星期四。"""
    nov1 = date(year, 11, 1)
    first_thursday = nov1 + timedelta(days=(3 - nov1.weekday()) % 7)
    thanksgiving = first_thursday + timedelta(days=21)
    return thanksgiving + timedelta(days=1)


def promo_marks(start: date, end: date) -> list[PromoMark]:
    marks: list[PromoMark] = []
    for year in range(start.year, end.year + 1):
        dated = [(date(year, month, day), code) for month, day, code in _FIXED_PROMOS]
        dated.append((black_friday(year), "BLACK_FRIDAY"))
        dated.append((black_friday(year) + timedelta(days=3), "CYBER_MONDAY"))
        marks.extend(PromoMark(day, code) for day, code in dated if start <= day <= end)
    return marks


def fold_lines(groups: list[list[dict[str, Any]]]) -> list[CostLineTotal]:
    """同一币种下按成本项相加。任一行缺金额，这一项就不完整。"""
    folded: list[CostLineTotal] = []
    for code in LINE_CODES:
        amounts: list[Decimal] = []
        complete = bool(groups)
        for lines in groups:
            stored = next((item for item in lines if item.get("code") == code), None)
            amount = None if stored is None else stored.get("amount")
            if stored is None or not stored.get("complete") or amount is None:
                complete = False
                break
            amounts.append(Decimal(str(amount)))
        folded.append(
            CostLineTotal(
                code=code,
                amount=quantize(sum(amounts, Decimal("0"))) if complete else None,
                complete=complete,
            )
        )
    return folded


def _sum_optional(values: list[Decimal | None]) -> Decimal | None:
    if any(item is None for item in values):
        return None
    return quantize(sum((item for item in values if item is not None), Decimal("0")))


def fold_profit(rows: list[ProfitRow]) -> dict[tuple[int, date, str], tuple[ProfitRow, list[CostLineTotal]]]:
    grouped: dict[tuple[int, date, str], list[ProfitRow]] = {}
    for row in rows:
        grouped.setdefault((row.shop_id, row.stat_date, row.currency), []).append(row)
    folded: dict[tuple[int, date, str], tuple[ProfitRow, list[CostLineTotal]]] = {}
    for key, group in grouped.items():
        complete = all(item.complete for item in group)
        books = {item.book_currency for item in group}
        book_currency = next(iter(books)) if len(books) == 1 else ""
        book_ok = complete and book_currency != ""
        sample = group[0]
        merged = ProfitRow(
            shop_id=sample.shop_id,
            stat_date=sample.stat_date,
            currency=sample.currency,
            book_currency=book_currency,
            revenue=quantize(sum((item.revenue for item in group), Decimal("0"))),
            book_revenue=_sum_optional([item.book_revenue for item in group]) if book_ok else None,
            net_profit=_sum_optional([item.net_profit for item in group]) if complete else None,
            book_net_profit=_sum_optional([item.book_net_profit for item in group]) if book_ok else None,
            complete=complete,
            quantity=sum(item.quantity for item in group),
            lines=[],
        )
        folded[key] = (merged, fold_lines([item.lines for item in group]))
    return folded


def assemble_shop_days(
    *,
    orders: list[OrderFact],
    profits: list[ProfitRow],
    ads: list[AdFact],
    returns: list[ReturnFact],
    shops: dict[int, tuple[str, str]],
    book_currency: str,
) -> list[ShopDay]:
    profit_map = fold_profit(profits)
    keys: set[tuple[int, date, str]] = set()
    keys.update((item.shop_id, item.stat_date, item.currency) for item in orders)
    keys.update(profit_map)
    keys.update((item.shop_id, item.stat_date, item.currency) for item in ads)
    keys.update((item.shop_id, item.stat_date, item.currency) for item in returns)
    order_map = {(item.shop_id, item.stat_date, item.currency): item for item in orders}
    ad_map = {(item.shop_id, item.stat_date, item.currency): item for item in ads}
    return_map = {(item.shop_id, item.stat_date, item.currency): item for item in returns}
    days: list[ShopDay] = []
    for key in sorted(keys, key=lambda item: (item[1], item[0], item[2])):
        shop = shops.get(key[0])
        order = order_map.get(key)
        if shop is not None:
            platform_code, site_code = shop
        elif order is not None:
            platform_code, site_code = order.platform_code, order.site_code
        else:
            continue
        profit = profit_map.get(key)
        ad = ad_map.get(key)
        returned = return_map.get(key)
        days.append(
            ShopDay(
                shop_id=key[0],
                platform_code=platform_code,
                site_code=site_code,
                stat_date=key[1],
                currency=key[2],
                book_currency=profit[0].book_currency or book_currency if profit else book_currency,
                order_count=0 if order is None else order.order_count,
                gmv=Decimal("0") if order is None else quantize(order.gmv),
                book_gmv=None if profit is None else profit[0].book_revenue,
                net_profit=None if profit is None or not profit[0].complete else profit[0].net_profit,
                book_net_profit=None if profit is None else profit[0].book_net_profit,
                profit_complete=bool(profit and profit[0].complete),
                on_time=0 if order is None else order.on_time,
                late=0 if order is None else order.late,
                return_count=0 if returned is None else returned.count,
                ad_spend=None if ad is None else quantize(ad.spend),
                ad_sales=None if ad is None else quantize(ad.sales),
                ad_loss_count=0 if ad is None else ad.loss_count,
                lines=fold_lines([]) if profit is None else profit[1],
            )
        )
    return days


def _money_key(currency: str, book_currency: str) -> tuple[str, str]:
    return currency, book_currency


def summarize(rows: list[ShopDay]) -> WindowSummary:
    order_count = sum(row.order_count for row in rows)
    on_time = sum(row.on_time for row in rows)
    late = sum(row.late for row in rows)
    returns = sum(row.return_count for row in rows)
    currencies = _currency_rollups(rows)
    platforms = _platform_rollups(rows)
    days = _day_rollups(rows)
    lines: dict[str, list[CostLineTotal]] = {}
    for currency in {row.currency for row in rows}:
        same = [row for row in rows if row.currency == currency]
        groups = [
            [{"code": line.code, "amount": line.amount, "complete": line.complete} for line in row.lines]
            for row in same
        ]
        lines[currency] = fold_lines(groups)
    return WindowSummary(
        order_count=order_count,
        on_time=on_time,
        late=late,
        returns=returns,
        currencies=currencies,
        platforms=platforms,
        days=days,
        lines=lines,
    )


def _currency_rollups(rows: list[ShopDay]) -> list[CurrencyRollup]:
    grouped: dict[tuple[str, str], list[ShopDay]] = {}
    for row in rows:
        grouped.setdefault(_money_key(row.currency, row.book_currency), []).append(row)
    rollups: list[CurrencyRollup] = []
    for (currency, book_currency), group in sorted(grouped.items()):
        complete = all(item.profit_complete for item in group)
        spend_values = [item.ad_spend for item in group]
        sales_values = [item.ad_sales for item in group]
        has_ads = any(item is not None for item in spend_values)
        spend = quantize(sum((item for item in spend_values if item is not None), Decimal("0"))) if has_ads else None
        sales = quantize(sum((item for item in sales_values if item is not None), Decimal("0"))) if has_ads else None
        net_profit = _sum_optional([item.net_profit for item in group]) if complete else None
        purchase = _line_amount(group, "PURCHASE")
        first_mile = _line_amount(group, "FIRST_MILE")
        gmv = quantize(sum((item.gmv for item in group), Decimal("0")))
        rollups.append(
            CurrencyRollup(
                currency=currency,
                book_currency=book_currency,
                order_count=sum(item.order_count for item in group),
                gmv=gmv,
                book_gmv=_sum_optional([item.book_gmv for item in group]) if complete else None,
                net_profit=net_profit,
                book_net_profit=_sum_optional([item.book_net_profit for item in group]) if complete else None,
                profit_complete=complete,
                ad_spend=spend,
                ad_sales=sales,
                ad_loss_count=sum(item.ad_loss_count for item in group),
                net_margin=None if net_profit is None else ratio(net_profit, gmv),
                acos=None if spend is None or sales is None else ratio(spend, sales),
                roas=None if spend is None or sales is None else ratio(sales, spend),
                profit_roi=None if net_profit is None else profit_roi(net_profit, purchase, first_mile),
            )
        )
    return rollups


def _line_amount(rows: list[ShopDay], code: str) -> Decimal | None:
    amounts: list[Decimal] = []
    for row in rows:
        line = next((item for item in row.lines if item.code == code), None)
        if line is None or not line.complete or line.amount is None:
            return None
        amounts.append(line.amount)
    return quantize(sum(amounts, Decimal("0")))


def _platform_rollups(rows: list[ShopDay]) -> list[PlatformRollup]:
    grouped: dict[tuple[str, str, str], list[ShopDay]] = {}
    for row in rows:
        grouped.setdefault((row.platform_code, row.site_code, row.currency), []).append(row)
    rollups: list[PlatformRollup] = []
    for (platform_code, site_code, currency), group in sorted(grouped.items()):
        complete = all(item.profit_complete for item in group)
        rollups.append(
            PlatformRollup(
                platform_code=platform_code,
                site_code=site_code,
                currency=currency,
                book_currency=group[0].book_currency,
                order_count=sum(item.order_count for item in group),
                gmv=quantize(sum((item.gmv for item in group), Decimal("0"))),
                book_gmv=_sum_optional([item.book_gmv for item in group]) if complete else None,
                net_profit=_sum_optional([item.net_profit for item in group]) if complete else None,
                profit_complete=complete,
                on_time=sum(item.on_time for item in group),
                late=sum(item.late for item in group),
                return_count=sum(item.return_count for item in group),
            )
        )
    return rollups


def _day_rollups(rows: list[ShopDay]) -> list[DayRollup]:
    grouped: dict[tuple[date, str, str], list[ShopDay]] = {}
    for row in rows:
        grouped.setdefault((row.stat_date, row.platform_code, row.currency), []).append(row)
    rollups: list[DayRollup] = []
    for (stat_date, platform_code, currency), group in sorted(grouped.items()):
        complete = all(item.profit_complete for item in group)
        rollups.append(
            DayRollup(
                stat_date=stat_date,
                platform_code=platform_code,
                currency=currency,
                order_count=sum(item.order_count for item in group),
                gmv=quantize(sum((item.gmv for item in group), Decimal("0"))),
                net_profit=_sum_optional([item.net_profit for item in group]) if complete else None,
                profit_complete=complete,
            )
        )
    return rollups


def profit_roi(net_profit: Decimal, purchase: Decimal | None, first_mile: Decimal | None) -> Decimal | None:
    if purchase is None or first_mile is None:
        return None
    return ratio(net_profit, purchase + first_mile)


def fulfillment_rate(on_time: int, late: int) -> Decimal | None:
    return ratio(on_time, on_time + late)


def return_rate(returns: int, orders: int) -> Decimal | None:
    return ratio(returns, orders)


def turnover_days(on_hand: int, sold_qty: int, window_days: int) -> Decimal | None:
    if window_days <= 0:
        return None
    return ratio(Decimal(on_hand) * Decimal(window_days), sold_qty)


def assess_inventory(stocks: list[SkuStock]) -> InventoryHealth:
    on_hand = 0
    stockout = 0
    below_safe = 0
    stale = 0
    stale_by_currency: dict[str, list[Decimal | None]] = {}
    for item in stocks:
        on_hand += item.available
        if item.available == 0:
            stockout += 1
            continue
        if item.available < item.safe_stock:
            below_safe += 1
        if item.sold_qty == 0:
            stale += 1
            currency = (item.currency or "").strip()
            if not currency:
                continue
            amount = None if item.purchase_price is None else quantize(item.purchase_price * Decimal(item.available))
            stale_by_currency.setdefault(currency, []).append(amount)
    stale_amounts = [
        StaleAmount(
            currency=currency,
            amount=None
            if any(amount is None for amount in amounts)
            else quantize(sum((amount for amount in amounts if amount is not None), Decimal("0"))),
            complete=all(amount is not None for amount in amounts),
        )
        for currency, amounts in sorted(stale_by_currency.items())
    ]
    return InventoryHealth(
        on_hand_qty=on_hand,
        stockout_sku_count=stockout,
        below_safe_sku_count=below_safe,
        stale_sku_count=stale,
        stale_amounts=stale_amounts,
    )


def rank_skus(rows: list[SkuRank], *, limit: int) -> tuple[list[SkuRank], list[SkuRank], list[SkuRank]]:
    """只排完整日利润。亏损是净利为负，不把缺失利润当成 0。"""
    ready = [row for row in rows if row.complete and row.net_profit is not None]
    ordered = sorted(ready, key=lambda item: item.net_profit or Decimal("0"), reverse=True)
    losses = [row for row in ordered if row.loss]
    return ordered[:limit], list(reversed(ordered[-limit:])), losses[:limit]


def line_payload(lines: list[CostLineTotal]) -> list[dict[str, object]]:
    return [
        {
            "code": line.code,
            "amount": None if line.amount is None else money_text(line.amount),
            "complete": line.complete,
        }
        for line in lines
    ]


def parse_lines(payload: list[dict[str, object]]) -> list[CostLineTotal]:
    return fold_lines([payload]) if payload else fold_lines([])


def percent_text(value: Decimal | None) -> str | None:
    percent = as_percent(value)
    if percent is None:
        return None
    return money_text(percent)
