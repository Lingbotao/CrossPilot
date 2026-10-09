"""汇率、日利润与瀑布。金额出参是十进制字符串。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from app.models.finance import RATE_BASES
from app.schemas.compliance import clean_currency
from app.schemas.landed_cost import clean_channel, clean_method
from app.schemas.locale import clean_market
from app.schemas.product import clean_text, decimal_text, optional_money


def clean_basis(value: object) -> str:
    if not isinstance(value, str) or value.strip().upper() not in RATE_BASES:
        raise ValueError("汇率口径必须是下单、结算或记账")
    return value.strip().upper()


def clean_grain(value: object) -> str:
    if not isinstance(value, str) or value.strip().lower() not in {"day", "week", "month"}:
        raise ValueError("粒度必须是日、周或月")
    return value.strip().lower()


class RateCreate(BaseModel):
    base_currency: str
    quote_currency: str
    rate: Decimal
    basis: str
    effective_on: date
    source: str

    @field_validator("base_currency", "quote_currency", mode="before")
    @classmethod
    def _currency(cls, value: object) -> str:
        return clean_currency(value)

    @field_validator("rate", mode="before")
    @classmethod
    def _rate(cls, value: object) -> Decimal:
        parsed = decimal_text(value)
        if parsed <= 0:
            raise ValueError("汇率必须大于 0")
        return parsed

    @field_validator("basis", mode="before")
    @classmethod
    def _basis(cls, value: object) -> str:
        return clean_basis(value)

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, value: object) -> str:
        text = clean_text(value, limit=2000)
        if not text:
            raise ValueError("来源必须填写")
        return text

    @model_validator(mode="after")
    def _distinct(self) -> RateCreate:
        if self.base_currency == self.quote_currency:
            raise ValueError("两个币种不能相同")
        return self


class RateView(BaseModel):
    id: int
    base_currency: str
    quote_currency: str
    rate: str
    basis: str
    effective_on: date
    source: str
    locked: bool

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class MaterializeRequest(BaseModel):
    market: str
    channel: str
    first_mile_method: str
    date_from: date
    date_to: date
    book_currency: str = "CNY"
    storage_days: int | None = Field(default=None, ge=0)
    shipment_cost: Decimal | None = None
    shipment_currency: str | None = None
    shipment_weight_g: Decimal | None = None
    shipment_volume_cm3: Decimal | None = None
    shipment_value: Decimal | None = None

    @field_validator("market", mode="before")
    @classmethod
    def _market(cls, value: object) -> str:
        return clean_market(value)

    @field_validator("channel", mode="before")
    @classmethod
    def _channel(cls, value: object) -> str:
        return clean_channel(value, allow_any=False)

    @field_validator("first_mile_method", mode="before")
    @classmethod
    def _method(cls, value: object) -> str:
        return clean_method(value)

    @field_validator("book_currency", mode="before")
    @classmethod
    def _book(cls, value: object) -> str:
        return clean_currency(value)

    @field_validator("shipment_currency", mode="before")
    @classmethod
    def _ship_currency(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        return clean_currency(value)

    @field_validator(
        "shipment_cost",
        "shipment_weight_g",
        "shipment_volume_cm3",
        "shipment_value",
        mode="before",
    )
    @classmethod
    def _money(cls, value: object) -> Decimal | None:
        return optional_money(value)

    @model_validator(mode="after")
    def _span(self) -> MaterializeRequest:
        if self.date_to < self.date_from:
            raise ValueError("结束日不能早于开始日")
        if (self.date_to - self.date_from).days > 92:
            raise ValueError("一次最多重算 92 天")
        return self


class MaterializeView(BaseModel):
    rows: int
    unmatched_items: int
    incomplete_rows: int


class ProfitRowView(BaseModel):
    sku_id: str
    shop_id: str
    period_start: date
    period_end: date
    currency: str
    book_currency: str
    revenue: str
    cost_total: str | None
    net_profit: str | None
    net_margin_percent: str | None
    book_revenue: str | None
    book_net_profit: str | None
    fx_gain: str | None
    quantity: int
    complete: bool


class WaterfallStepView(BaseModel):
    code: str
    label: str
    amount: str | None
    running: str | None
    memo: bool
    formula: str


class WaterfallView(BaseModel):
    currency: str
    book_currency: str
    steps: list[WaterfallStepView]
