"""批量刊登与批量改价的请求响应。售价出参是十进制字符串。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models.listing import BATCH_ITEM_STATUSES, BATCH_KINDS, BATCH_STATUSES
from app.schemas.common import MoneyMixin
from app.schemas.listing import parse_id
from app.schemas.product import clean_text, optional_money


def _id_list(value: object, *, limit: int) -> list[int]:
    if not isinstance(value, list):
        raise ValueError("必须是列表")
    if len(value) > limit:
        raise ValueError("数量过多")
    return [parse_id(item) for item in value]


def _currency(value: object) -> str:
    text = clean_text(value, limit=3)
    if text is None:
        raise ValueError("币种不能为空")
    upper = text.upper()
    if len(upper) != 3 or not upper.isalpha():
        raise ValueError("币种必须是 3 位字母")
    return upper


class PublishBatchCreate(BaseModel):
    sku_ids: list[int] = Field(min_length=1)
    shop_ids: list[int] = Field(min_length=1)
    price: Decimal
    currency: str

    @field_validator("sku_ids", "shop_ids", mode="before")
    @classmethod
    def _ids(cls, value: object) -> list[int]:
        return _id_list(value, limit=1000)

    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, value: object) -> Decimal:
        parsed = optional_money(value)
        if parsed is None:
            raise ValueError("售价不能为空")
        return parsed

    @field_validator("currency", mode="before")
    @classmethod
    def _ccy(cls, value: object) -> str:
        return _currency(value)


class PriceBatchPreview(BaseModel):
    listing_ids: list[int] = Field(min_length=1)
    price: Decimal
    currency: str

    @field_validator("listing_ids", mode="before")
    @classmethod
    def _ids(cls, value: object) -> list[int]:
        return _id_list(value, limit=1000)

    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, value: object) -> Decimal:
        parsed = optional_money(value)
        if parsed is None:
            raise ValueError("售价不能为空")
        return parsed

    @field_validator("currency", mode="before")
    @classmethod
    def _ccy(cls, value: object) -> str:
        return _currency(value)


class PriceBatchCreate(PriceBatchPreview):
    confirmed: bool = False


class ListingBatchItemView(MoneyMixin):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_id: int
    shop_id: int
    listing_id: int | None
    status: str
    error_message: str | None
    price_before: Decimal | None
    currency_before: str | None
    price_after: Decimal | None
    currency_after: str | None

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        if value not in BATCH_ITEM_STATUSES:
            raise ValueError("状态不正确")
        return value

    @field_serializer("id", "sku_id", "shop_id", "listing_id")
    def _ids(self, value: int | None) -> str | None:
        if value is None:
            return None
        return str(value)


class ListingBatchView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    status: str
    total: int
    succeeded: int
    failed: int
    skipped: int
    items: list[ListingBatchItemView]
    created_at: datetime
    updated_at: datetime

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        if value not in BATCH_KINDS:
            raise ValueError("批次类型不正确")
        return value

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        if value not in BATCH_STATUSES:
            raise ValueError("状态不正确")
        return value

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class PricePreviewLine(MoneyMixin):
    listing_id: int
    sku_id: int
    shop_id: int
    price_before: Decimal | None
    currency_before: str | None
    price_after: Decimal
    currency_after: str
    needs_confirm: bool

    @field_serializer("listing_id", "sku_id", "shop_id")
    def _ids(self, value: int) -> str:
        return str(value)


class PricePreview(BaseModel):
    needs_confirm: bool
    lines: list[PricePreviewLine]
