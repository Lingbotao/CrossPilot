"""商品主数据请求与响应。金额和尺寸出参是十进制字符串。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models.product import PRODUCT_STATUSES
from app.schemas.common import MoneyMixin

_COST_FIELDS = frozenset({"purchase_price", "currency"})


def decimal_text(value: object) -> Decimal:
    """只接受 Decimal 或十进制字符串，拒绝 float / int，避免 JSON number 丢精度。"""
    if isinstance(value, Decimal):
        return value
    if not isinstance(value, str):
        raise ValueError("必须是十进制字符串")
    try:
        return Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError("不是合法数字") from exc


def positive_decimal(value: object) -> Decimal:
    parsed = decimal_text(value)
    if parsed <= 0:
        raise ValueError("必须大于 0")
    return parsed


def optional_money(value: object) -> Decimal | None:
    if value is None:
        return None
    parsed = decimal_text(value)
    if parsed < 0:
        raise ValueError("不能为负")
    return parsed


def clean_text(value: object, *, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("必须是字符串")
    text = value.strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"不能超过 {limit} 个字符")
    return text


def clean_specs(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("规格必须是对象")
    if len(value) > 20:
        raise ValueError("规格属性过多")
    cleaned: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError("规格名和规格值都必须是字符串")
        name = key.strip()
        text = item.strip()
        if not name or len(name) > 32 or len(text) > 128:
            raise ValueError("规格名或规格值不合法")
        cleaned[name] = text
    return cleaned


class SkuWrite(BaseModel):
    sku_code: str = Field(min_length=1, max_length=64)
    barcode: str | None = Field(default=None, max_length=64)
    spec_attrs: dict[str, str] = Field(default_factory=dict)
    weight_g: Decimal
    length_cm: Decimal
    width_cm: Decimal
    height_cm: Decimal
    purchase_price: Decimal | None = None
    currency: str | None = None

    @field_validator("sku_code", mode="before")
    @classmethod
    def _sku_code(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("SKU 编码必须是字符串")
        text = value.strip()
        if not text:
            raise ValueError("SKU 编码不能为空")
        return text

    @field_validator("barcode", mode="before")
    @classmethod
    def _barcode(cls, value: object) -> str | None:
        return clean_text(value, limit=64)

    @field_validator("spec_attrs", mode="before")
    @classmethod
    def _specs(cls, value: object) -> dict[str, str]:
        return clean_specs(value)

    @field_validator("weight_g", "length_cm", "width_cm", "height_cm", mode="before")
    @classmethod
    def _measures(cls, value: object) -> Decimal:
        return positive_decimal(value)

    @field_validator("purchase_price", mode="before")
    @classmethod
    def _price(cls, value: object) -> Decimal | None:
        return optional_money(value)

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, value: object) -> str | None:
        text = clean_text(value, limit=3)
        if text is None:
            return None
        upper = text.upper()
        if len(upper) != 3 or not upper.isalpha():
            raise ValueError("币种必须是 3 位字母")
        return upper


class SkuPatch(BaseModel):
    sku_code: str | None = Field(default=None, max_length=64)
    barcode: str | None = None
    spec_attrs: dict[str, str] | None = None
    weight_g: Decimal | None = None
    length_cm: Decimal | None = None
    width_cm: Decimal | None = None
    height_cm: Decimal | None = None
    purchase_price: Decimal | None = None
    currency: str | None = None

    @field_validator("sku_code", mode="before")
    @classmethod
    def _sku_code(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("SKU 编码必须是字符串")
        text = value.strip()
        if not text:
            raise ValueError("SKU 编码不能为空")
        return text

    @field_validator("barcode", mode="before")
    @classmethod
    def _barcode(cls, value: object) -> str | None:
        return clean_text(value, limit=64)

    @field_validator("spec_attrs", mode="before")
    @classmethod
    def _specs(cls, value: object) -> dict[str, str] | None:
        if value is None:
            return None
        return clean_specs(value)

    @field_validator("weight_g", "length_cm", "width_cm", "height_cm", mode="before")
    @classmethod
    def _measures(cls, value: object) -> Decimal | None:
        if value is None:
            return None
        return positive_decimal(value)

    @field_validator("purchase_price", mode="before")
    @classmethod
    def _price(cls, value: object) -> Decimal | None:
        return optional_money(value)

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, value: object) -> str | None:
        text = clean_text(value, limit=3)
        if text is None:
            return None
        upper = text.upper()
        if len(upper) != 3 or not upper.isalpha():
            raise ValueError("币种必须是 3 位字母")
        return upper


class SpuCreate(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    brand: str | None = Field(default=None, max_length=128)
    material: str | None = Field(default=None, max_length=128)
    purpose: str | None = Field(default=None, max_length=256)
    status: str = "DRAFT"
    skus: list[SkuWrite] = Field(default_factory=list, max_length=200)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("标题不能为空")
        text = value.strip()
        if len(text) > 256:
            raise ValueError("标题过长")
        return text

    @field_validator("brand", mode="before")
    @classmethod
    def _brand(cls, value: object) -> str | None:
        return clean_text(value, limit=128)

    @field_validator("material", mode="before")
    @classmethod
    def _material(cls, value: object) -> str | None:
        return clean_text(value, limit=128)

    @field_validator("purpose", mode="before")
    @classmethod
    def _purpose(cls, value: object) -> str | None:
        return clean_text(value, limit=256)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        if value not in PRODUCT_STATUSES:
            raise ValueError("状态不正确")
        return value


class SpuPatch(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    brand: str | None = None
    material: str | None = None
    purpose: str | None = None
    status: str | None = None

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("标题不能为空")
        text = value.strip()
        if len(text) > 256:
            raise ValueError("标题过长")
        return text

    @field_validator("brand", mode="before")
    @classmethod
    def _brand(cls, value: object) -> str | None:
        return clean_text(value, limit=128)

    @field_validator("material", mode="before")
    @classmethod
    def _material(cls, value: object) -> str | None:
        return clean_text(value, limit=128)

    @field_validator("purpose", mode="before")
    @classmethod
    def _purpose(cls, value: object) -> str | None:
        return clean_text(value, limit=256)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in PRODUCT_STATUSES:
            raise ValueError("状态不正确")
        return value


class SkuView(MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: int
    spu_id: int
    sku_code: str
    barcode: str | None
    spec_attrs: dict[str, Any]
    weight_g: Decimal
    length_cm: Decimal
    width_cm: Decimal
    height_cm: Decimal
    purchase_price: Decimal | None
    currency: str | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("id", "spu_id")
    def _ids(self, value: int) -> str:
        return str(value)


class SpuListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    brand: str | None
    status: str
    sku_count: int
    created_at: datetime
    updated_at: datetime

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class SpuDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    brand: str | None
    material: str | None
    purpose: str | None
    status: str
    skus: list[SkuView]
    created_at: datetime
    updated_at: datetime

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


def cost_fields_sent(fields_set: set[str]) -> bool:
    return bool(_COST_FIELDS & fields_set)
