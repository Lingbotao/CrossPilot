"""经营看板的请求响应。金额和比率出参是十进制字符串，比率用百分数。"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class DashboardRebuildRequest(BaseModel):
    date_from: date
    date_to: date


class DashboardRebuildView(BaseModel):
    shop_days: int
    inventory_as_of: date


class MoneyChangeView(BaseModel):
    currency: str
    book_currency: str
    order_count: int
    gmv: str
    book_gmv: str | None = None
    net_profit: str | None = None
    book_net_profit: str | None = None
    net_margin: str | None = None
    gmv_change: str | None = None
    gmv_yoy: str | None = None
    profit_change: str | None = None
    profit_yoy: str | None = None
    ad_spend: str | None = None
    ad_sales: str | None = None
    acos: str | None = None
    roas: str | None = None
    profit_roi: str | None = None
    roi_formula: str
    ad_loss_count: int


class DayPointView(BaseModel):
    stat_date: date
    platform_code: str
    currency: str
    order_count: int
    gmv: str
    net_profit: str | None = None


class PromoView(BaseModel):
    stat_date: date
    code: str


class TrendView(BaseModel):
    days: list[DayPointView]
    promos: list[PromoView]


class DashboardOverviewView(BaseModel):
    order_count: int
    order_change: str | None = None
    order_yoy: str | None = None
    on_time_rate: str | None = None
    return_rate: str | None = None
    currencies: list[MoneyChangeView]
    days: list[DayPointView]
    promos: list[PromoView]


class PlatformCompareView(BaseModel):
    platform_code: str
    site_code: str
    currency: str
    book_currency: str
    order_count: int
    gmv: str
    book_gmv: str | None = None
    net_profit: str | None = None
    on_time_rate: str | None = None
    return_rate: str | None = None


class SkuRankView(BaseModel):
    sku_id: str
    spu_id: str
    sku_code: str
    currency: str
    book_currency: str
    quantity: int
    revenue: str
    net_profit: str | None = None
    book_net_profit: str | None = None
    net_margin: str | None = None
    loss: bool


class SkuRankingView(BaseModel):
    top: list[SkuRankView]
    bottom: list[SkuRankView]
    loss: list[SkuRankView]
    incomplete_count: int


class CostLineView(BaseModel):
    code: str
    currency: str
    amount: str | None = None
    share: str | None = None
    complete: bool


class StaleAmountView(BaseModel):
    currency: str
    amount: str | None = None
    complete: bool


class InventoryHealthView(BaseModel):
    as_of: date | None = None
    on_hand_qty: int
    stockout_sku_count: int
    below_safe_sku_count: int
    stale_sku_count: int
    sold_qty: int
    turnover_days: str | None = None
    turnover_formula: str
    stale_amounts: list[StaleAmountView]


class AdsRoiView(BaseModel):
    currency: str
    ad_spend: str | None = None
    ad_sales: str | None = None
    acos: str | None = None
    roas: str | None = None
    profit_roi: str | None = None
    roi_formula: str
    ad_loss_count: int


class FulfillmentView(BaseModel):
    platform_code: str
    site_code: str
    currency: str
    order_count: int
    on_time: int
    late: int
    on_time_rate: str | None = None
    return_count: int
    return_rate: str | None = None
