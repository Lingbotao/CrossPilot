"""广告同步与分析的请求响应。金额和比率出参是十进制字符串。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import money_to_str
from app.schemas.listing import parse_id


class AdsSyncRequest(BaseModel):
    shop_id: int
    date_from: date | None = None
    date_to: date | None = None

    @field_validator("shop_id", mode="before")
    @classmethod
    def _shop(cls, value: object) -> int:
        return parse_id(value)


class AdsSyncView(BaseModel):
    task_id: str
    shop_id: str
    campaigns: int
    days: int
    keywords: int
    loss_count: int


class AdsTotalView(BaseModel):
    currency: str
    spend: str | None = None
    sales: str | None = None
    acos: str | None = None
    roas: str | None = None


class AdsOverviewView(BaseModel):
    impressions: int
    clicks: int
    orders: int
    ctr: str | None = None
    cvr: str | None = None
    totals: list[AdsTotalView] = Field(default_factory=list)


class AdsCampaignView(BaseModel):
    id: str
    shop_id: str
    platform_code: str
    platform_campaign_id: str
    name: str
    campaign_type: str
    status: str
    currency: str
    sku_id: str | None = None
    impressions: int
    clicks: int
    orders: int
    ctr: str | None = None
    cvr: str | None = None
    spend: str | None = None
    sales: str | None = None
    acos: str | None = None
    roas: str | None = None
    loss_flag: bool


class AdsDayView(BaseModel):
    stat_date: date
    impressions: int
    clicks: int
    orders: int
    ctr: str | None = None
    cvr: str | None = None
    spend: str | None = None
    sales: str | None = None
    acos: str | None = None
    roas: str | None = None
    gross_margin: str | None = None
    loss_flag: bool
    suggestion_code: str


class AdsCampaignDetail(BaseModel):
    campaign: AdsCampaignView
    days: list[AdsDayView]


class AdsKeywordView(BaseModel):
    id: str
    campaign_id: str
    campaign_name: str
    platform_code: str
    stat_date: date
    keyword: str
    impressions: int
    clicks: int
    orders: int
    currency: str
    spend: str | None = None
    sales: str | None = None
    suggest_negative: bool


class AdsLossView(BaseModel):
    id: str
    campaign_id: str
    campaign_name: str
    platform_code: str
    shop_id: str
    stat_date: date
    currency: str
    spend: str
    sales: str
    acos: str | None = None
    gross_margin: str | None = None
    suggestion_code: str


class AdsSkuProfitView(BaseModel):
    sku_id: str | None = None
    currency: str
    actual_spend: str
    estimated_ads: str | None = None
    net_profit: str | None = None
    net_after_ads: str | None = None
    formula: str
    complete: bool


def shown_money(value: Decimal | None, *, visible: bool) -> str | None:
    if not visible or value is None:
        return None
    return money_to_str(value)


def shown_id(value: int | None) -> str | None:
    if value is None:
        return None
    return str(value)
