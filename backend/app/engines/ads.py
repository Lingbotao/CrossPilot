"""广告日指标、亏损标记和扣除实际广告费后的净利。纯函数，不访问数据库。

毛利率用落地成本，不含广告费，避免和 ACOS 互相扣减。
净利仍是售价减去落地成本、平台费用、广告费和退货损失。这里只把日利润里的预估广告费换成实际花费，不另造利润口径。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.engines.landed_cost import LANDED_CODES, money_text, quantize

CAMPAIGN_STATUSES: tuple[str, ...] = ("ENABLED", "PAUSED", "ARCHIVED", "UNKNOWN")
CAMPAIGN_TYPES: tuple[str, ...] = ("SPONSORED_PRODUCT", "SPONSORED_BRAND", "SPONSORED_DISPLAY", "OTHER")
SUGGESTION_NONE = ""
SUGGESTION_NO_SALES = "NO_SALES"
SUGGESTION_ACOS_ABOVE_MARGIN = "ACOS_ABOVE_MARGIN"
SUGGESTION_MARGIN_MISSING = "MARGIN_MISSING"
SUGGESTION_CODES: tuple[str, ...] = (
    SUGGESTION_NONE,
    SUGGESTION_NO_SALES,
    SUGGESTION_ACOS_ABOVE_MARGIN,
    SUGGESTION_MARGIN_MISSING,
)
_HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class DayMetrics:
    impressions: int
    clicks: int
    orders: int
    spend: Decimal
    sales: Decimal
    ctr: Decimal | None
    acos: Decimal | None
    roas: Decimal | None
    cvr: Decimal | None
    gross_margin: Decimal | None
    loss_flag: bool
    suggestion_code: str


def ratio(numerator: Decimal | int, denominator: Decimal | int) -> Decimal | None:
    """分子除以分母。分母为 0 时没有比率，不用 0 顶上。"""
    base = Decimal(denominator)
    if base == 0:
        return None
    return quantize(Decimal(numerator) / base)


def as_percent(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return quantize(value * _HUNDRED)


def line_amount(lines: list[dict[str, object]], code: str) -> Decimal | None:
    stored = next((item for item in lines if item.get("code") == code), None)
    if stored is None or not stored.get("complete") or stored.get("amount") is None:
        return None
    return quantize(Decimal(str(stored["amount"])))


def landed_total(lines: list[dict[str, object]]) -> Decimal | None:
    total = Decimal("0")
    for code in LANDED_CODES:
        amount = line_amount(lines, code)
        if amount is None:
            return None
        total += amount
    return quantize(total)


def gross_margin_ratio(revenue: Decimal, lines: list[dict[str, object]]) -> Decimal | None:
    """(销售额 - 落地成本) / 销售额。落地成本缺任何一项就不算毛利率。"""
    if revenue <= 0:
        return None
    landed = landed_total(lines)
    if landed is None:
        return None
    return quantize((revenue - landed) / revenue)


def plan_day(
    *,
    impressions: int,
    clicks: int,
    orders: int,
    spend: Decimal,
    sales: Decimal,
    gross_margin: Decimal | None,
) -> DayMetrics:
    spend_q = quantize(spend)
    sales_q = quantize(sales)
    acos = ratio(spend_q, sales_q)
    loss, suggestion = _loss(spend=spend_q, sales=sales_q, acos=acos, gross_margin=gross_margin)
    return DayMetrics(
        impressions=impressions,
        clicks=clicks,
        orders=orders,
        spend=spend_q,
        sales=sales_q,
        ctr=ratio(clicks, impressions),
        acos=acos,
        roas=ratio(sales_q, spend_q),
        cvr=ratio(orders, clicks),
        gross_margin=None if gross_margin is None else quantize(gross_margin),
        loss_flag=loss,
        suggestion_code=suggestion,
    )


def suggest_negative(*, spend: Decimal, orders: int) -> bool:
    """有花费且没有转化的词进入建议否定清单。不设金额门槛，避免把阈值写死在代码里。"""
    return spend > 0 and orders == 0


def combine_sku_ads(
    *,
    net_profits: list[Decimal | None],
    estimated_ads: list[Decimal | None],
    actual_spend: Decimal,
) -> tuple[Decimal | None, str, bool]:
    """账面净利加回预估广告费，再减去实际花费。"""
    if not net_profits or any(item is None for item in net_profits) or any(item is None for item in estimated_ads):
        return None, "日利润或预估广告费不完整，不能改写成实际广告费。", False
    net = quantize(sum((item for item in net_profits if item is not None), Decimal("0")))
    estimated = quantize(sum((item for item in estimated_ads if item is not None), Decimal("0")))
    actual = quantize(actual_spend)
    value = quantize(net + estimated - actual)
    return value, f"{money_text(net)} + {money_text(estimated)} - {money_text(actual)}", True


def _loss(
    *,
    spend: Decimal,
    sales: Decimal,
    acos: Decimal | None,
    gross_margin: Decimal | None,
) -> tuple[bool, str]:
    if spend <= 0:
        return False, SUGGESTION_NONE
    if sales == 0:
        return True, SUGGESTION_NO_SALES
    if gross_margin is None:
        return False, SUGGESTION_MARGIN_MISSING
    if acos is not None and acos > gross_margin:
        return True, SUGGESTION_ACOS_ABOVE_MARGIN
    return False, SUGGESTION_NONE
