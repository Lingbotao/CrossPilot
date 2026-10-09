"""落地成本请求与响应。金额出参是十进制字符串。比例费用的入参是百分数。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from app.engines.landed_cost import CHARGES, FEE_CODES, FIRST_MILE_METHODS, LINE_CODES
from app.schemas.compliance import clean_currency
from app.schemas.listing import parse_optional_id
from app.schemas.locale import clean_market
from app.schemas.product import clean_text, decimal_text, optional_money

_SOURCE_LIMIT = 2000
_LABEL_LIMIT = 64
_HS_LIMIT = 10
_CHARGE_FOR: dict[str, set[str]] = {
    "FIRST_MILE": {"PER_KG", "PER_CBM", "FIXED"},
    "BROKERAGE": {"FIXED"},
    "COMMISSION": {"RATE"},
    "PAYMENT": {"RATE"},
    "FULFILLMENT": {"FIXED", "RATE"},
    "ADS": {"RATE"},
    "RETURN_RATE": {"RATE"},
    "RETURN_LOSS": {"RATE"},
    "STORAGE": {"PER_CBM_DAY"},
    "FX_RESERVE": {"RATE"},
}
CALC_CHANNELS = tuple(item for item in ("AIR", "SEA_FCL", "SEA_LCL", "EXPRESS", "PACKET"))


def clean_fee_code(value: object) -> str:
    if not isinstance(value, str) or value.strip().upper() not in FEE_CODES:
        raise ValueError("费用项不在支持列表中")
    return value.strip().upper()


def clean_charge(value: object) -> str:
    if not isinstance(value, str) or value.strip().upper() not in CHARGES:
        raise ValueError("计费方式不在支持列表中")
    return value.strip().upper()


def clean_channel(value: object, *, allow_any: bool) -> str:
    if not isinstance(value, str):
        raise ValueError("渠道不在支持列表中")
    text = value.strip().upper()
    allowed = set(CALC_CHANNELS)
    if allow_any:
        allowed.add("*")
    if text not in allowed:
        raise ValueError("渠道不在支持列表中")
    return text


def clean_method(value: object) -> str:
    if not isinstance(value, str) or value.strip().upper() not in FIRST_MILE_METHODS:
        raise ValueError("头程分摊方式不在支持列表中")
    return value.strip().upper()


def clean_hs(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("HS 编码必须是数字")
    text = value.strip()
    if not text:
        return ""
    if text.isdigit() and 2 <= len(text) <= _HS_LIMIT:
        return text
    raise ValueError("HS 编码必须是 2 到 10 位数字")


class FeeCreate(BaseModel):
    market: str
    channel: str
    fee_code: str
    label: str = "*"
    charge: str
    amount: Decimal
    currency: str | None = None
    volumetric_divisor: int | None = Field(default=None, ge=1)
    effective_from: date
    effective_to: date | None = None
    source: str

    @field_validator("market", mode="before")
    @classmethod
    def _market(cls, value: object) -> str:
        return clean_market(value)

    @field_validator("channel", mode="before")
    @classmethod
    def _channel(cls, value: object) -> str:
        return clean_channel(value, allow_any=True)

    @field_validator("fee_code", mode="before")
    @classmethod
    def _code(cls, value: object) -> str:
        return clean_fee_code(value)

    @field_validator("label", mode="before")
    @classmethod
    def _label(cls, value: object) -> str:
        text = clean_text(value, limit=_LABEL_LIMIT)
        return text or "*"

    @field_validator("charge", mode="before")
    @classmethod
    def _charge(cls, value: object) -> str:
        return clean_charge(value)

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, value: object) -> Decimal:
        parsed = decimal_text(value)
        if parsed < 0:
            raise ValueError("金额不能为负")
        return parsed

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        return clean_currency(value)

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, value: object) -> str:
        text = clean_text(value, limit=_SOURCE_LIMIT)
        if not text:
            raise ValueError("来源必须填写")
        return text

    @model_validator(mode="after")
    def _pair(self) -> FeeCreate:
        allowed = _CHARGE_FOR[self.fee_code]
        if self.charge not in allowed:
            raise ValueError("这个费用项不能使用该计费方式")
        if self.charge == "RATE" and self.currency is not None:
            raise ValueError("按比例计费不要填写币种")
        if self.charge != "RATE" and self.currency is None:
            raise ValueError("固定金额必须填写币种")
        if self.volumetric_divisor is not None and not (self.fee_code == "FIRST_MILE" and self.charge == "PER_KG"):
            raise ValueError("只有按公斤计的头程可以填写体积重除数")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("结束日必须晚于生效日")
        return self


class FeeView(BaseModel):
    id: int
    market: str
    channel: str
    fee_code: str
    label: str
    charge: str
    amount: str
    amount_percent: str | None
    currency: str | None
    volumetric_divisor: int | None
    effective_from: date
    effective_to: date | None
    version: int
    status: str
    source: str
    verified_by: str
    verified_at: datetime

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class CalcRequest(BaseModel):
    market: str
    selling_currency: str
    channel: str
    first_mile_method: str
    name: str = ""
    sku_id: int | None = None
    selling_price: Decimal | None = None
    purchase_amount: Decimal | None = None
    purchase_currency: str | None = None
    fx_rate: Decimal | None = None
    fx_source: str = ""
    weight_g: Decimal | None = None
    volume_cm3: Decimal | None = None
    length_cm: Decimal | None = None
    width_cm: Decimal | None = None
    height_cm: Decimal | None = None
    hs_code: str = ""
    declared_value: Decimal | None = None
    declared_currency: str | None = None
    shipment_cost: Decimal | None = None
    shipment_currency: str | None = None
    shipment_weight_g: Decimal | None = None
    shipment_volume_cm3: Decimal | None = None
    shipment_value: Decimal | None = None
    storage_days: int | None = Field(default=None, ge=0)
    as_of: date | None = None

    @field_validator("market", mode="before")
    @classmethod
    def _market(cls, value: object) -> str:
        return clean_market(value)

    @field_validator("selling_currency", mode="before")
    @classmethod
    def _selling_currency(cls, value: object) -> str:
        return clean_currency(value)

    @field_validator("purchase_currency", "declared_currency", "shipment_currency", mode="before")
    @classmethod
    def _currency(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        return clean_currency(value)

    @field_validator("channel", mode="before")
    @classmethod
    def _channel(cls, value: object) -> str:
        return clean_channel(value, allow_any=False)

    @field_validator("first_mile_method", mode="before")
    @classmethod
    def _method(cls, value: object) -> str:
        return clean_method(value)

    @field_validator("name", "fx_source", mode="before")
    @classmethod
    def _text(cls, value: object) -> str:
        return clean_text(value, limit=80) or ""

    @field_validator("sku_id", mode="before")
    @classmethod
    def _sku(cls, value: object) -> int | None:
        return parse_optional_id(value)

    @field_validator(
        "selling_price",
        "purchase_amount",
        "fx_rate",
        "weight_g",
        "volume_cm3",
        "length_cm",
        "width_cm",
        "height_cm",
        "declared_value",
        "shipment_cost",
        "shipment_weight_g",
        "shipment_volume_cm3",
        "shipment_value",
        mode="before",
    )
    @classmethod
    def _money(cls, value: object) -> Decimal | None:
        return optional_money(value)

    @field_validator("hs_code", mode="before")
    @classmethod
    def _hs(cls, value: object) -> str:
        return clean_hs(value)

    @model_validator(mode="after")
    def _selling_currency_required(self) -> CalcRequest:
        if self.fx_rate is not None and self.fx_rate <= 0:
            raise ValueError("汇率必须大于 0")
        return self


class CostLineView(BaseModel):
    code: str
    label: str
    amount: str | None
    currency: str
    formula: str
    source: str
    complete: bool


class CalcView(BaseModel):
    id: int
    name: str
    market: str
    currency: str
    lines: list[CostLineView]
    landed_cost: str | None
    net_profit: str | None
    net_margin_percent: str | None
    roi_percent: str | None
    complete: bool
    profit_complete: bool

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class CompareView(BaseModel):
    id: int
    left: CalcView
    right: CalcView

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class CompareRequest(BaseModel):
    left: CalcRequest
    right: CalcRequest


def clean_line_code(value: object) -> str:
    if not isinstance(value, str) or value.strip().upper() not in LINE_CODES:
        raise ValueError("成本项不在支持列表中")
    return value.strip().upper()


class ToggleWrite(BaseModel):
    market: str
    channel: str
    line_code: str
    enabled: bool

    @field_validator("market", mode="before")
    @classmethod
    def _market(cls, value: object) -> str:
        return clean_market(value)

    @field_validator("channel", mode="before")
    @classmethod
    def _channel(cls, value: object) -> str:
        return clean_channel(value, allow_any=True)

    @field_validator("line_code", mode="before")
    @classmethod
    def _line(cls, value: object) -> str:
        return clean_line_code(value)


class ToggleView(BaseModel):
    id: int
    market: str
    channel: str
    line_code: str
    enabled: bool

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class PricingRequest(CalcRequest):
    target_margin_percent: Decimal
    period_fixed_cost: Decimal | None = None

    @field_validator("target_margin_percent", mode="before")
    @classmethod
    def _margin(cls, value: object) -> Decimal:
        parsed = decimal_text(value)
        if parsed < 0 or parsed > _HUNDRED:
            raise ValueError("目标净利率必须在 0 到 100 之间")
        return parsed

    @field_validator("period_fixed_cost", mode="before")
    @classmethod
    def _period_cost(cls, value: object) -> Decimal | None:
        parsed = optional_money(value)
        if parsed is not None and parsed < 0:
            raise ValueError("期间固定成本不能为负")
        return parsed


class PricePointView(BaseModel):
    target_margin_percent: str
    selling_price: str | None
    net_margin_percent: str | None
    reachable: bool
    formula: str


class PricingView(BaseModel):
    id: int
    suggested_price: str | None
    break_even_price: str | None
    break_even_quantity: str | None
    reachable: bool
    formula: str
    quantity_formula: str
    currency: str
    curve: list[PricePointView]
    lines: list[CostLineView]
    gaps: list[str]
    complete: bool

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


_HUNDRED = Decimal("100")
