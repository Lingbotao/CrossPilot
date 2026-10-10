"""采购、收货和头程的请求响应。金额出参是十进制字符串。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.engines.purchase import ALLOC_METHODS, PO_STATUSES, RECEIPT_DISPOSITIONS, SETTLEMENT_TYPES
from app.models.purchase import REAL_CHANNELS
from app.schemas.common import money_to_str
from app.schemas.listing import parse_id
from app.schemas.product import decimal_text

_CURRENCY = 3


def _currency(value: object) -> str:
    if not isinstance(value, str) or len(value.strip()) != _CURRENCY:
        raise ValueError("币种必须是 3 位字符")
    return value.strip().upper()


def _money(value: object) -> Decimal:
    parsed = decimal_text(value)
    if parsed < 0:
        raise ValueError("金额不能为负")
    return parsed


class SupplierWrite(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    contact: str = ""
    settlement_type: str
    credit_days: int = Field(default=0, ge=0)
    rating: int | None = Field(default=None, ge=1, le=5)

    @field_validator("name", "contact")
    @classmethod
    def _text(cls, value: str) -> str:
        return value.strip()

    @field_validator("settlement_type")
    @classmethod
    def _settlement(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if cleaned not in SETTLEMENT_TYPES:
            raise ValueError("结算方式不支持")
        return cleaned


class SupplierPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    contact: str | None = None
    settlement_type: str | None = None
    credit_days: int | None = Field(default=None, ge=0)
    rating: int | None = Field(default=None, ge=1, le=5)

    @field_validator("name", "contact")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("settlement_type")
    @classmethod
    def _settlement(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        if cleaned not in SETTLEMENT_TYPES:
            raise ValueError("结算方式不支持")
        return cleaned


class SkuSupplierLink(BaseModel):
    sku_id: int
    is_default: bool = False

    @field_validator("sku_id", mode="before")
    @classmethod
    def _sku(cls, value: object) -> int:
        return parse_id(value)


class SkuSupplierReplace(BaseModel):
    links: list[SkuSupplierLink] = Field(default_factory=list)


class SkuSupplierView(BaseModel):
    sku_id: int
    sku_code: str
    is_default: bool

    @field_serializer("sku_id")
    def _sku(self, value: int) -> str:
        return str(value)


class SupplierView(BaseModel):
    id: int
    name: str
    contact: str
    settlement_type: str
    credit_days: int
    rating: int | None
    skus: list[SkuSupplierView]

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class PurchaseLineWrite(BaseModel):
    sku_id: int
    quantity: int = Field(gt=0)
    unit_price: Decimal
    tax_included: bool = False
    expected_on: date | None = None

    @field_validator("sku_id", mode="before")
    @classmethod
    def _sku(cls, value: object) -> int:
        return parse_id(value)

    @field_validator("unit_price", mode="before")
    @classmethod
    def _price(cls, value: object) -> Decimal:
        return _money(value)


class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    warehouse_id: int
    currency: str
    expected_on: date | None = None
    note: str = ""
    lines: list[PurchaseLineWrite] = Field(min_length=1)

    @field_validator("supplier_id", "warehouse_id", mode="before")
    @classmethod
    def _ids(cls, value: object) -> int:
        return parse_id(value)

    @field_validator("currency")
    @classmethod
    def _ccy(cls, value: str) -> str:
        return _currency(value)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str) -> str:
        return value.strip()


class PurchaseLinePatch(BaseModel):
    sku_id: int
    quantity: int = Field(gt=0)
    unit_price: Decimal
    tax_included: bool = False
    expected_on: date | None = None

    @field_validator("sku_id", mode="before")
    @classmethod
    def _sku(cls, value: object) -> int:
        return parse_id(value)

    @field_validator("unit_price", mode="before")
    @classmethod
    def _price(cls, value: object) -> Decimal:
        return _money(value)


class PurchaseOrderPatch(BaseModel):
    expected_on: date | None = None
    note: str | None = None
    lines: list[PurchaseLinePatch] | None = None

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class PurchaseLineView(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    quantity: int
    received_qty: int
    short_qty: int
    open_qty: int
    unit_price: str | None
    currency: str | None
    tax_included: bool
    expected_on: date | None

    @field_serializer("id", "sku_id")
    def _ids(self, value: int) -> str:
        return str(value)


class PurchaseOrderView(BaseModel):
    id: int
    supplier_id: int
    supplier_name: str
    warehouse_id: int
    warehouse_name: str
    warehouse_type: str
    status: str
    currency: str | None
    total_amount: str | None
    expected_on: date | None
    note: str
    lines: list[PurchaseLineView]

    @field_serializer("id", "supplier_id", "warehouse_id")
    def _ids(self, value: int) -> str:
        return str(value)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        if value not in PO_STATUSES:
            raise ValueError("采购状态不支持")
        return value


class ReceiptLineWrite(BaseModel):
    item_id: int
    quantity: int = Field(default=0, ge=0)

    @field_validator("item_id", mode="before")
    @classmethod
    def _item(cls, value: object) -> int:
        return parse_id(value)


class ReceiptCreate(BaseModel):
    disposition: str
    allow_over: bool = False
    note: str = ""
    lines: list[ReceiptLineWrite] = Field(min_length=1)

    @field_validator("disposition")
    @classmethod
    def _disposition(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if cleaned not in RECEIPT_DISPOSITIONS:
            raise ValueError("收货处理方式不支持")
        return cleaned

    @field_validator("note")
    @classmethod
    def _note(cls, value: str) -> str:
        return value.strip()


class ReceiptView(BaseModel):
    id: int
    purchase_order_id: int
    disposition: str
    note: str
    order: PurchaseOrderView

    @field_serializer("id", "purchase_order_id")
    def _ids(self, value: int) -> str:
        return str(value)


class InTransitLineView(BaseModel):
    region: str
    warehouse_id: int
    warehouse_name: str
    warehouse_type: str
    sku_id: int
    sku_code: str
    quantity: int

    @field_serializer("warehouse_id", "sku_id")
    def _ids(self, value: int) -> str:
        return str(value)


class InTransitView(BaseModel):
    domestic_qty: int
    overseas_qty: int
    lines: list[InTransitLineView]


class ShipmentLineWrite(BaseModel):
    sku_id: int
    quantity: int = Field(gt=0)

    @field_validator("sku_id", mode="before")
    @classmethod
    def _sku(cls, value: object) -> int:
        return parse_id(value)


class ShipmentCreate(BaseModel):
    forwarder: str = Field(min_length=1, max_length=128)
    channel: str
    container_no: str = ""
    destination_market: str
    cost_total: Decimal
    currency: str
    alloc_method: str
    storage_days: int = Field(default=0, ge=0)
    purchase_order_id: int | None = None
    from_warehouse_id: int | None = None
    to_warehouse_id: int | None = None
    lines: list[ShipmentLineWrite] = Field(min_length=1)

    @field_validator("forwarder", "container_no")
    @classmethod
    def _text(cls, value: str) -> str:
        return value.strip()

    @field_validator("channel")
    @classmethod
    def _channel(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if cleaned not in REAL_CHANNELS:
            raise ValueError("物流渠道不支持")
        return cleaned

    @field_validator("destination_market")
    @classmethod
    def _market(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if len(cleaned) != 2:
            raise ValueError("目的国必须是 2 位字符")
        return cleaned

    @field_validator("currency")
    @classmethod
    def _ccy(cls, value: str) -> str:
        return _currency(value)

    @field_validator("alloc_method")
    @classmethod
    def _method(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if cleaned not in ALLOC_METHODS:
            raise ValueError("头程分摊方式不支持")
        return cleaned

    @field_validator("cost_total", mode="before")
    @classmethod
    def _cost(cls, value: object) -> Decimal:
        parsed = _money(value)
        if parsed <= 0:
            raise ValueError("头程总额必须大于 0")
        return parsed

    @field_validator("purchase_order_id", "from_warehouse_id", "to_warehouse_id", mode="before")
    @classmethod
    def _optional_id(cls, value: object) -> int | None:
        if value is None or value == "":
            return None
        return parse_id(value)


class AllocationView(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    quantity: int
    allocated_cost: str | None
    currency: str | None
    method: str
    formula: str
    source: str

    @field_serializer("id", "sku_id")
    def _ids(self, value: int) -> str:
        return str(value)


class CostPoolView(BaseModel):
    id: int
    sku_id: int
    quantity: int
    currency: str | None
    purchase_unit: str | None
    first_mile_unit: str | None
    duty_unit: str | None
    import_tax_unit: str | None
    brokerage_unit: str | None
    storage_unit: str | None
    fx_reserve_unit: str | None
    landed_unit: str | None
    lines: list[dict[str, str | bool | None]]
    source: str

    @field_serializer("id", "sku_id")
    def _ids(self, value: int) -> str:
        return str(value)


class ShipmentView(BaseModel):
    id: int
    purchase_order_id: int | None
    from_warehouse_id: int | None
    to_warehouse_id: int | None
    forwarder: str
    channel: str
    container_no: str
    destination_market: str
    cost_total: str | None
    currency: str | None
    alloc_method: str
    storage_days: int
    status: str
    allocations: list[AllocationView]
    pools: list[CostPoolView]

    @field_serializer("id", "purchase_order_id", "from_warehouse_id", "to_warehouse_id")
    def _ids(self, value: int | None) -> str | None:
        return None if value is None else str(value)


class ReplenishmentConvert(BaseModel):
    sku_ids: list[int] = Field(min_length=1)
    warehouse_id: int
    supplier_id: int | None = None
    currency: str
    window_days: int = Field(default=30, ge=1, le=365)
    unit_price: Decimal | None = None

    @field_validator("sku_ids", mode="before")
    @classmethod
    def _skus(cls, value: object) -> list[int]:
        if not isinstance(value, list):
            raise ValueError("SKU 列表不合法")
        return [parse_id(item) for item in value]

    @field_validator("warehouse_id", mode="before")
    @classmethod
    def _warehouse(cls, value: object) -> int:
        return parse_id(value)

    @field_validator("supplier_id", mode="before")
    @classmethod
    def _supplier(cls, value: object) -> int | None:
        if value is None or value == "":
            return None
        return parse_id(value)

    @field_validator("currency")
    @classmethod
    def _ccy(cls, value: str) -> str:
        return _currency(value)

    @field_validator("unit_price", mode="before")
    @classmethod
    def _price(cls, value: object) -> Decimal | None:
        if value is None or value == "":
            return None
        return _money(value)


def shown_money(value: Decimal | None, *, visible: bool) -> str | None:
    if not visible:
        return None
    return money_to_str(value)
