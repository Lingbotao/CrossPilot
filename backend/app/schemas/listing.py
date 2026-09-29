"""Listing 与类目模板的请求响应。售价出参是十进制字符串。"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from app.models.listing import LISTING_STATUSES
from app.schemas.common import MoneyMixin
from app.schemas.product import clean_text, optional_money

_ATTR_KEY = re.compile(r"[A-Za-z0-9_]{1,64}")
_CODE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def parse_id(value: object) -> int:
    if isinstance(value, bool | float):
        raise ValueError("ID 不合法")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("ID 不合法")
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        if parsed <= 0:
            raise ValueError("ID 不合法")
        return parsed
    raise ValueError("ID 不合法")


def parse_optional_id(value: object) -> int | None:
    if value is None or value == "":
        return None
    return parse_id(value)


def clean_code(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError("必须是字符串")
    text = value.strip()
    if not _CODE.fullmatch(text) or len(text) > limit:
        raise ValueError("只允许字母、数字、下划线和连字符")
    return text


def clean_attr_values(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("属性值必须是对象")
    if len(value) > 30:
        raise ValueError("属性过多")
    cleaned: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError("属性名和属性值都必须是字符串")
        name = key.strip()
        text = item.strip()
        if not _ATTR_KEY.fullmatch(name) or len(text) > 256:
            raise ValueError("属性名或属性值不合法")
        cleaned[name] = text
    return cleaned


class AttrTemplateItem(BaseModel):
    key: str
    label: str
    required: bool = False

    @field_validator("key", mode="before")
    @classmethod
    def _key(cls, value: object) -> str:
        if not isinstance(value, str) or not _ATTR_KEY.fullmatch(value.strip()):
            raise ValueError("属性键只允许字母、数字和下划线")
        return value.strip()

    @field_validator("label", mode="before")
    @classmethod
    def _label(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("属性名称不能为空")
        text = value.strip()
        if len(text) > 64:
            raise ValueError("属性名称过长")
        return text


def _unique_attrs(items: list[AttrTemplateItem]) -> list[AttrTemplateItem]:
    keys = [item.key for item in items]
    if len(keys) != len(set(keys)):
        raise ValueError("属性键重复")
    return items


class CategoryMappingCreate(BaseModel):
    platform_code: str = Field(min_length=1, max_length=32)
    site_code: str = Field(min_length=1, max_length=8)
    platform_category_id: str = Field(min_length=1, max_length=128)
    local_category_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    attrs_template: list[AttrTemplateItem] = Field(default_factory=list, max_length=30)

    @field_validator("platform_code", mode="before")
    @classmethod
    def _platform(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("平台不能为空")
        return value.strip().lower()

    @field_validator("site_code", mode="before")
    @classmethod
    def _site(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("站点不能为空")
        return value.strip().upper()

    @field_validator("platform_category_id", mode="before")
    @classmethod
    def _platform_category(cls, value: object) -> str:
        text = clean_text(value, limit=128)
        if text is None:
            raise ValueError("平台类目不能为空")
        return text

    @field_validator("local_category_code", mode="before")
    @classmethod
    def _local_code(cls, value: object) -> str:
        return clean_code(value, limit=64)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> str:
        text = clean_text(value, limit=128)
        if text is None:
            raise ValueError("模板名称不能为空")
        return text

    @field_validator("attrs_template")
    @classmethod
    def _attrs(cls, value: list[AttrTemplateItem]) -> list[AttrTemplateItem]:
        return _unique_attrs(value)


class CategoryMappingPatch(BaseModel):
    platform_code: str | None = None
    site_code: str | None = None
    platform_category_id: str | None = None
    local_category_code: str | None = None
    name: str | None = None
    attrs_template: list[AttrTemplateItem] | None = None

    @field_validator("platform_code", mode="before")
    @classmethod
    def _platform(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("平台不能为空")
        return value.strip().lower()

    @field_validator("site_code", mode="before")
    @classmethod
    def _site(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("站点不能为空")
        return value.strip().upper()

    @field_validator("platform_category_id", mode="before")
    @classmethod
    def _platform_category(cls, value: object) -> str | None:
        if value is None:
            return None
        text = clean_text(value, limit=128)
        if text is None:
            raise ValueError("平台类目不能为空")
        return text

    @field_validator("local_category_code", mode="before")
    @classmethod
    def _local_code(cls, value: object) -> str | None:
        if value is None:
            return None
        return clean_code(value, limit=64)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> str | None:
        if value is None:
            return None
        text = clean_text(value, limit=128)
        if text is None:
            raise ValueError("模板名称不能为空")
        return text

    @field_validator("attrs_template")
    @classmethod
    def _attrs(cls, value: list[AttrTemplateItem] | None) -> list[AttrTemplateItem] | None:
        if value is None:
            return None
        return _unique_attrs(value)


class CategoryMappingView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform_code: str
    site_code: str
    platform_category_id: str
    local_category_code: str
    name: str
    attrs_template: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class ListingWriteBase(BaseModel):
    platform_product_id: str | None = None
    platform_sku_id: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    attr_values: dict[str, str] = Field(default_factory=dict)
    status: str | None = None

    @field_validator("platform_product_id", "platform_sku_id", mode="before")
    @classmethod
    def _platform_ids(cls, value: object) -> str | None:
        return clean_text(value, limit=128)

    @field_validator("price", mode="before")
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

    @field_validator("attr_values", mode="before")
    @classmethod
    def _attrs(cls, value: object) -> dict[str, str]:
        return clean_attr_values(value)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in LISTING_STATUSES:
            raise ValueError("状态不正确")
        return value


class ListingCreate(ListingWriteBase):
    sku_id: int
    shop_id: int
    category_mapping_id: int | None = None

    @field_validator("sku_id", "shop_id", mode="before")
    @classmethod
    def _ids(cls, value: object) -> int:
        return parse_id(value)

    @field_validator("category_mapping_id", mode="before")
    @classmethod
    def _template_id(cls, value: object) -> int | None:
        return parse_optional_id(value)

    @model_validator(mode="after")
    def _price_pair(self) -> Self:
        if (self.price is None) != (self.currency is None):
            raise ValueError("售价和币种需要同时填写")
        return self


class ListingPatch(ListingWriteBase):
    sku_id: int | None = None
    shop_id: int | None = None
    category_mapping_id: int | None = None

    @field_validator("sku_id", "shop_id", mode="before")
    @classmethod
    def _ids(cls, value: object) -> int | None:
        return parse_optional_id(value)

    @field_validator("category_mapping_id", mode="before")
    @classmethod
    def _template_id(cls, value: object) -> int | None:
        return parse_optional_id(value)


class ListingShopOption(BaseModel):
    id: int
    shop_name: str
    platform_code: str
    site_code: str

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class ListingView(MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_id: int
    sku_code: str
    shop_id: int
    shop_name: str
    platform_code: str
    site_code: str
    category_mapping_id: int | None
    local_category_code: str | None
    template_name: str | None
    platform_product_id: str | None
    platform_sku_id: str | None
    price: Decimal | None
    currency: str | None
    attr_values: dict[str, str]
    status: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("id", "sku_id", "shop_id", "category_mapping_id")
    def _ids(self, value: int | None) -> str | None:
        if value is None:
            return None
        return str(value)
