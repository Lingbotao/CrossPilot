"""税率、认证台账与提醒的契约。税率出参同时给比例和百分数，前端不做除法。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from app.models.compliance import CERT_TYPES, TAX_TYPES
from app.schemas.common import money_to_str
from app.schemas.listing import parse_id
from app.schemas.locale import clean_market
from app.schemas.product import decimal_text

_SOURCE_LIMIT = 2000
_CATEGORY_LIMIT = 64
_CERT_NO_LIMIT = 128


def _required_text(value: object, *, empty: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(empty)
    text = value.strip()
    if not text:
        raise ValueError(empty)
    if len(text) > limit:
        raise ValueError(f"不能超过 {limit} 个字符")
    return text


def clean_tax_type(value: object) -> str:
    if not isinstance(value, str) or value.strip().upper() not in TAX_TYPES:
        raise ValueError("税种不在支持列表中")
    return value.strip().upper()


def clean_cert_type(value: object) -> str:
    if not isinstance(value, str) or value.strip().upper() not in CERT_TYPES:
        raise ValueError("认证类型不在支持列表中")
    return value.strip().upper()


def clean_hs_pattern(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("HS 模式必须是 * 或 2 到 10 位数字")
    text = value.strip()
    if text == "*":
        return text
    if text.isdigit() and 2 <= len(text) <= 10:
        return text
    raise ValueError("HS 模式必须是 * 或 2 到 10 位数字")


def clean_currency(value: object) -> str:
    if not isinstance(value, str) or len(value.strip()) != 3 or not value.strip().isalpha():
        raise ValueError("币种必须是 3 位字母")
    return value.strip().upper()


def _id_text(value: int) -> str:
    return str(value)


class TaxRuleCreate(BaseModel):
    country: str
    tax_type: str
    hs_code_pattern: str = "*"
    rate: Decimal
    basis_numerator: int = Field(default=1, ge=1)
    basis_denominator: int = Field(default=1, ge=1)
    threshold_amount: Decimal | None = None
    threshold_currency: str | None = None
    effective_from: date
    effective_to: date | None = None
    source: str

    @field_validator("country", mode="before")
    @classmethod
    def _country(cls, value: object) -> str:
        return clean_market(value)

    @field_validator("tax_type", mode="before")
    @classmethod
    def _tax_type(cls, value: object) -> str:
        return clean_tax_type(value)

    @field_validator("hs_code_pattern", mode="before")
    @classmethod
    def _pattern(cls, value: object) -> str:
        return clean_hs_pattern(value)

    @field_validator("rate", mode="before")
    @classmethod
    def _rate(cls, value: object) -> Decimal:
        return decimal_text(value)

    @field_validator("threshold_amount", mode="before")
    @classmethod
    def _threshold(cls, value: object) -> Decimal | None:
        if value is None or value == "":
            return None
        parsed = decimal_text(value)
        if parsed < 0:
            raise ValueError("门槛不能为负")
        return parsed

    @field_validator("threshold_currency", mode="before")
    @classmethod
    def _currency(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        return clean_currency(value)

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, value: object) -> str:
        return _required_text(value, empty="来源必须填写", limit=_SOURCE_LIMIT)

    @model_validator(mode="after")
    def _pair(self) -> TaxRuleCreate:
        has_amount = self.threshold_amount is not None
        has_currency = self.threshold_currency is not None
        if has_amount != has_currency:
            raise ValueError("门槛金额和币种必须同时填写")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("结束日必须晚于生效日")
        return self


class TaxRuleView(BaseModel):
    id: int
    country: str
    tax_type: str
    hs_code_pattern: str
    rate: str
    rate_percent: str
    basis_numerator: int
    basis_denominator: int
    threshold_amount: str | None
    threshold_currency: str | None
    effective_from: date
    effective_to: date | None
    version: int
    status: str
    source: str
    verified_by: str
    verified_at: datetime

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return _id_text(value)


class CertificateCreate(BaseModel):
    sku_id: int
    market: str
    cert_type: str
    cert_no: str
    issued_at: date
    expires_at: date

    @field_validator("sku_id", mode="before")
    @classmethod
    def _sku(cls, value: object) -> int:
        return parse_id(value)

    @field_validator("market", mode="before")
    @classmethod
    def _market(cls, value: object) -> str:
        return clean_market(value)

    @field_validator("cert_type", mode="before")
    @classmethod
    def _cert_type(cls, value: object) -> str:
        return clean_cert_type(value)

    @field_validator("cert_no", mode="before")
    @classmethod
    def _cert_no(cls, value: object) -> str:
        return _required_text(value, empty="证书号必须填写", limit=_CERT_NO_LIMIT)

    @model_validator(mode="after")
    def _dates(self) -> CertificateCreate:
        if self.expires_at < self.issued_at:
            raise ValueError("到期日不能早于签发日")
        return self


class CertificateView(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    market: str
    cert_type: str
    cert_no: str
    issued_at: date
    expires_at: date
    object_key: str | None
    content_type: str | None
    updated_at: datetime

    @field_serializer("id", "sku_id")
    def _ids(self, value: int) -> str:
        return _id_text(value)


class CertificateImportResult(BaseModel):
    imported: int
    updated: int


class CertRequirementCreate(BaseModel):
    market: str
    category_code: str
    cert_type: str
    source: str

    @field_validator("market", mode="before")
    @classmethod
    def _market(cls, value: object) -> str:
        return clean_market(value)

    @field_validator("category_code", mode="before")
    @classmethod
    def _category(cls, value: object) -> str:
        return _required_text(value, empty="类目代码必须填写", limit=_CATEGORY_LIMIT)

    @field_validator("cert_type", mode="before")
    @classmethod
    def _cert_type(cls, value: object) -> str:
        return clean_cert_type(value)

    @field_validator("source", mode="before")
    @classmethod
    def _source(cls, value: object) -> str:
        return _required_text(value, empty="来源必须填写", limit=_SOURCE_LIMIT)


class CertRequirementView(BaseModel):
    id: int
    market: str
    category_code: str
    cert_type: str
    source: str
    status: str
    updated_at: datetime

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return _id_text(value)


class CertGapView(BaseModel):
    cert_type: str
    reason: str


class ComplianceAlertView(BaseModel):
    kind: str
    level: str
    ref_id: int
    due_on: date
    summary: str
    emailed_at: datetime | None

    @field_serializer("ref_id")
    def _ref(self, value: int) -> str:
        return _id_text(value)


def threshold_text(amount: Decimal | None) -> str | None:
    return money_to_str(amount)


__all__ = [
    "CertGapView",
    "CertRequirementCreate",
    "CertRequirementView",
    "CertificateCreate",
    "CertificateImportResult",
    "CertificateView",
    "ComplianceAlertView",
    "TaxRuleCreate",
    "TaxRuleView",
    "clean_cert_type",
    "clean_currency",
    "clean_hs_pattern",
    "clean_tax_type",
    "threshold_text",
]
