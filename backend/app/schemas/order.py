"""订单列表、详情、搜索与导出的响应模型。金额出参是字符串。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.schemas.common import MoneyMixin
from app.schemas.order_status import StatusSourceCode

LabelSize = Literal["A6", "100x150"]


class PartyView(BaseModel):
    name: str | None = None
    phone: str | None = None
    country: str | None = None


class AddressView(BaseModel):
    name: str | None = None
    phone: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    line1: str | None = None
    postal_code: str | None = None


class OrderItemView(MoneyMixin):
    id: int
    platform_sku_id: str
    platform_product_id: str
    item_name: str
    quantity: int
    unit_price: Decimal
    currency: str

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class OrderFeeView(MoneyMixin):
    id: int
    fee_type: str
    amount: Decimal
    currency: str
    source: str

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class ShipmentView(BaseModel):
    carrier: str | None = None
    tracking_no: str | None = None
    status: str | None = None
    failure_reason: str | None = None
    attempt: int | None = None
    shipped_at: datetime | None = None


class TimelineView(BaseModel):
    id: int
    from_status: str | None
    to_status: str
    platform_status: str
    operator_id: int | None
    source: StatusSourceCode
    remark: str | None
    created_at: datetime

    @field_serializer("id", "operator_id")
    def _ids(self, value: int | None) -> str | None:
        if value is None:
            return None
        return str(value)


class OrderListItem(MoneyMixin):
    id: int
    shop_id: int
    shop_name: str
    site_code: str
    platform_code: str
    platform_order_id: str
    unified_status: str
    platform_status: str
    currency: str
    total_amount: Decimal
    paid_at: datetime | None
    created_at: datetime
    buyer_name: str | None
    shipment_status: str | None
    failure_reason: str | None

    @field_serializer("id", "shop_id")
    def _ids(self, value: int) -> str:
        return str(value)


class OrderDetail(OrderListItem):
    item_amount: Decimal
    shipping_amount: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    shipped_at: datetime | None
    buyer: PartyView
    ship_to: AddressView
    items: list[OrderItemView]
    fees: list[OrderFeeView]
    shipment: ShipmentView
    timeline: list[TimelineView]
    tracking_no: str | None = None
    carrier: str | None = None


class BatchShipRequest(BaseModel):
    order_ids: list[str] = Field(min_length=1, max_length=500)
    carrier: str = Field(min_length=1, max_length=64)

    @field_validator("order_ids")
    @classmethod
    def _ids(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            text = value.strip()
            if not text.isascii() or not text.isdigit():
                raise ValueError("订单 ID 不正确")
            cleaned.append(text)
        return cleaned

    @field_validator("carrier")
    @classmethod
    def _carrier(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("请填写承运商")
        return text


class ShipItemResult(BaseModel):
    order_id: int
    platform_order_id: str
    ok: bool
    tracking_no: str | None = None
    message: str

    @field_serializer("order_id")
    def _id(self, value: int) -> str:
        return str(value)


class BatchShipResult(BaseModel):
    succeeded: int
    failed: int
    results: list[ShipItemResult]


class LabelRequest(BaseModel):
    order_ids: list[str] = Field(min_length=1, max_length=500)
    size: LabelSize

    @field_validator("order_ids")
    @classmethod
    def _ids(cls, values: list[str]) -> list[str]:
        return BatchShipRequest._ids(values)


class LabelSkip(BaseModel):
    order_id: int
    message: str
    platform_order_id: str | None = None

    @field_serializer("order_id")
    def _id(self, value: int) -> str:
        return str(value)


class FilePayload(BaseModel):
    filename: str
    content_type: str
    content_base64: str
    row_count: int = 0
    truncated: bool = False
    skipped: list[LabelSkip] = Field(default_factory=list)


def parse_order_id(value: str) -> int:
    text = value.strip()
    if not text.isascii() or not text.isdigit():
        raise ValueError("订单 ID 不正确")
    return int(text)


__all__ = [
    "AddressView",
    "BatchShipRequest",
    "BatchShipResult",
    "FilePayload",
    "LabelRequest",
    "LabelSize",
    "LabelSkip",
    "OrderDetail",
    "OrderFeeView",
    "OrderItemView",
    "OrderListItem",
    "PartyView",
    "ShipItemResult",
    "ShipmentView",
    "TimelineView",
    "parse_order_id",
]
