"""汇兑损益与日利润汇总。损益单列，不进商品净利。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.engines.landed_cost import LINE_CODES, LINE_LABELS, CostLine, LandedCostResult, money_text, quantize

_HUNDRED = Decimal("100")
GRAINS = ("day", "week", "month")


@dataclass(frozen=True)
class FxGain:
    amount: Decimal | None
    currency: str
    formula: str
    source: str
    complete: bool


@dataclass(frozen=True)
class DailyRollup:
    revenue: Decimal
    cost_total: Decimal | None
    net_profit: Decimal | None
    net_margin_percent: Decimal | None
    book_revenue: Decimal | None
    book_cost_total: Decimal | None
    book_net_profit: Decimal | None
    fx_gain: Decimal | None
    fx_formula: str
    fx_source: str
    lines: tuple[CostLine, ...]
    complete: bool


@dataclass(frozen=True)
class WaterfallStep:
    code: str
    label: str
    amount: Decimal | None
    running: Decimal | None
    memo: bool
    formula: str


def fx_gain_loss(
    *,
    foreign_amount: Decimal,
    order_rate: Decimal | None,
    settlement_rate: Decimal | None,
    order_source: str,
    settlement_source: str,
    book_currency: str,
    same_currency: bool = False,
) -> FxGain:
    """账面差额 = 外币金额 × (结算汇率 − 下单汇率)。汇率都是 1 外币折多少记账币。"""
    if same_currency:
        return FxGain(
            Decimal("0"),
            book_currency,
            "下单币种与记账币种相同，汇兑损益 = 0。不计入商品成本。",
            "币种相同",
            True,
        )
    if order_rate is None or settlement_rate is None or order_rate <= 0 or settlement_rate <= 0:
        return FxGain(None, book_currency, "缺少下单汇率或结算汇率，汇兑损益留空。", "未配置", False)
    amount = quantize(foreign_amount * (settlement_rate - order_rate))
    return FxGain(
        amount,
        book_currency,
        (
            f"汇兑损益 = {money_text(foreign_amount)} × (结算汇率 {money_text(settlement_rate)}"
            f" − 下单汇率 {money_text(order_rate)}) = {money_text(amount)} {book_currency}。"
            "不计入商品成本。"
        ),
        f"下单：{order_source}；结算：{settlement_source}",
        True,
    )


def rollup_daily(
    units: list[tuple[int, Decimal, LandedCostResult]],
    *,
    book_rate: Decimal | None,
    book_currency: str,
    fx: FxGain,
) -> DailyRollup:
    """把同一 SKU、店铺、日期、币种的订单行汇总成一行。缺记账汇率时成本和净利留空。"""
    revenue = quantize(sum((quantize(price * Decimal(quantity)) for quantity, price, _ in units), Decimal("0")))
    lines = _combine(units)
    ready = all(line.complete and line.amount is not None for line in lines)
    cost = quantize(sum((line.amount for line in lines if line.amount is not None), Decimal("0"))) if ready else None
    net = quantize(revenue - cost) if cost is not None else None
    margin = quantize(net / revenue * _HUNDRED) if net is not None and revenue > 0 else None
    if book_rate is None or book_rate <= 0:
        return DailyRollup(
            revenue,
            None,
            None,
            None,
            None,
            None,
            None,
            fx.amount,
            fx.formula,
            fx.source,
            lines,
            False,
        )
    book_revenue = quantize(revenue * book_rate)
    book_cost = quantize(cost * book_rate) if cost is not None else None
    book_net = quantize(net * book_rate) if net is not None else None
    return DailyRollup(
        revenue,
        cost,
        net,
        margin,
        book_revenue,
        book_cost,
        book_net,
        fx.amount,
        fx.formula,
        fx.source,
        lines,
        cost is not None,
    )


def period_bounds(stat_date: date, grain: str) -> tuple[date, date]:
    if grain == "day":
        return stat_date, stat_date
    if grain == "week":
        start = stat_date - timedelta(days=stat_date.weekday())
        return start, start + timedelta(days=6)
    if grain == "month":
        start = stat_date.replace(day=1)
        next_month = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
        return start, next_month - timedelta(days=1)
    raise ValueError("粒度必须是 day、week 或 month")


def build_waterfall(
    *,
    revenue: Decimal | None,
    lines: tuple[CostLine, ...] | list[CostLine],
    net_profit: Decimal | None,
    fx_gain: Decimal | None,
    fx_formula: str,
) -> tuple[WaterfallStep, ...]:
    """销售额逐项扣到净利。汇兑损益是表外一行，不改变净利。"""
    running = revenue
    steps = [WaterfallStep("REVENUE", "销售额", revenue, running, False, "销售额来自订单成交额。")]
    by_code = {line.code: line for line in lines}
    for code in LINE_CODES:
        line = by_code.get(code)
        if line is None or line.amount is None or running is None:
            steps.append(WaterfallStep(code, LINE_LABELS[code], None, None, False, "这一项不完整。"))
            running = None
            continue
        delta = quantize(-line.amount)
        running = quantize(running + delta)
        steps.append(WaterfallStep(code, line.label, delta, running, False, line.formula))
    steps.append(
        WaterfallStep("NET", "净利", net_profit, net_profit, False, "净利 = 销售额 − 各项成本。不含汇兑损益。")
    )
    steps.append(WaterfallStep("FX_GAIN", "汇兑损益", fx_gain, net_profit, True, fx_formula))
    return tuple(steps)


def _combine(units: list[tuple[int, Decimal, LandedCostResult]]) -> tuple[CostLine, ...]:
    combined: list[CostLine] = []
    for code in LINE_CODES:
        amounts: list[Decimal] = []
        sources: list[str] = []
        complete = True
        currency = ""
        for quantity, _, result in units:
            line = next(item for item in result.lines if item.code == code)
            currency = line.currency
            sources.append(line.source)
            if not line.complete or line.amount is None:
                complete = False
            else:
                amounts.append(quantize(line.amount * Decimal(quantity)))
        label = LINE_LABELS[code]
        unique_sources = "；".join(dict.fromkeys(sources))
        if not complete:
            combined.append(
                CostLine(code, label, None, currency, f"{label}有订单行不完整。", unique_sources or "未配置", False)
            )
            continue
        total = quantize(sum(amounts, Decimal("0")))
        combined.append(
            CostLine(
                code,
                label,
                total,
                currency,
                f"{label}合计 = {money_text(total)} {currency}。",
                unique_sources or "本次计算",
                True,
            )
        )
    return tuple(combined)
