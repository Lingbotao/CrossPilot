"""库存台账、仓库、流水、水位和补货建议。数量是整数，不是金额。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.models.inventory import FLOW_TYPES, HOLD_STATUSES, PUSH_STATUSES, WAREHOUSE_TYPES
from app.schemas.listing import parse_id

AdjustKind = Literal["INBOUND", "OUTBOUND", "TO_DEFECTIVE", "ADJUST"]
WarehouseType = Literal["LOCAL", "OVERSEAS", "FBA", "PLATFORM"]


def _country(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("国家必须是两位字母")
    code = value.strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError("国家必须是两位字母")
    return code


def _name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("名称不能为空")
    text = value.strip()
    if len(text) > 128:
        raise ValueError("名称不能超过 128 个字符")
    return text


class WarehouseWrite(BaseModel):
    name: str
    warehouse_type: WarehouseType
    country: str
    address: str = ""
    external_code: str | None = None
    is_default: bool = False

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: object) -> str:
        return _name(value)

    @field_validator("warehouse_type")
    @classmethod
    def _type(cls, value: object) -> str:
        if not isinstance(value, str) or value not in WAREHOUSE_TYPES:
            raise ValueError("仓库类型不支持")
        return value

    @field_validator("country")
    @classmethod
    def _clean_country(cls, value: object) -> str:
        return _country(value)

    @field_validator("address")
    @classmethod
    def _address(cls, value: object) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("地址必须是字符串")
        return value.strip()[:1000]

    @field_validator("external_code")
    @classmethod
    def _code(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("第三方仓编码必须是字符串")
        text = value.strip()
        if not text:
            return None
        if len(text) > 64:
            raise ValueError("第三方仓编码不能超过 64 个字符")
        return text


class WarehousePatch(BaseModel):
    name: str | None = None
    warehouse_type: WarehouseType | None = None
    country: str | None = None
    address: str | None = None
    external_code: str | None = None
    is_default: bool | None = None

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: object) -> str | None:
        if value is None:
            return None
        return _name(value)

    @field_validator("warehouse_type")
    @classmethod
    def _type(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value not in WAREHOUSE_TYPES:
            raise ValueError("仓库类型不支持")
        return value

    @field_validator("country")
    @classmethod
    def _clean_country(cls, value: object) -> str | None:
        if value is None:
            return None
        return _country(value)

    @field_validator("external_code")
    @classmethod
    def _code(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("第三方仓编码必须是字符串")
        text = value.strip()
        return text[:64] if text else None


class WarehouseView(BaseModel):
    id: int
    name: str
    warehouse_type: str
    country: str
    address: str
    external_code: str | None
    is_default: bool

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class InventoryView(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    warehouse_id: int
    warehouse_name: str
    warehouse_type: str
    available: int
    occupied: int
    in_transit: int
    defective: int
    safe_stock: int
    sellable: int
    version: int

    @field_serializer("id", "sku_id", "warehouse_id")
    def _ids(self, value: int) -> str:
        return str(value)


class InventoryAdjust(BaseModel):
    sku_id: int
    warehouse_id: int
    kind: AdjustKind
    quantity: int
    ref_type: str = ""
    ref_id: int | None = None

    @field_validator("sku_id", "warehouse_id", mode="before")
    @classmethod
    def _ids(cls, value: object) -> int:
        return parse_id(value)

    @field_validator("ref_id", mode="before")
    @classmethod
    def _ref(cls, value: object) -> int | None:
        if value is None or value == "":
            return None
        return parse_id(value)

    @field_validator("ref_type")
    @classmethod
    def _ref_type(cls, value: object) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("来源类型必须是字符串")
        return value.strip()[:32]


class InventorySafeStockPatch(BaseModel):
    safe_stock: int = Field(ge=0, le=1_000_000)


class FlowView(BaseModel):
    id: int
    created_at: datetime
    sku_id: int
    warehouse_id: int
    flow_type: str
    quantity: int
    ref_type: str
    ref_id: int | None
    before_qty: int
    after_qty: int

    @field_serializer("id", "sku_id", "warehouse_id")
    def _ids(self, value: int) -> str:
        return str(value)

    @field_serializer("ref_id")
    def _ref(self, value: int | None) -> str | None:
        if value is None:
            return None
        return str(value)

    @field_validator("flow_type")
    @classmethod
    def _type(cls, value: str) -> str:
        if value not in FLOW_TYPES:
            raise ValueError("流水类型不支持")
        return value


class PushRequest(BaseModel):
    sku_id: int

    @field_validator("sku_id", mode="before")
    @classmethod
    def _sku(cls, value: object) -> int:
        return parse_id(value)


class PushLogView(BaseModel):
    id: int
    shop_id: int
    sku_id: int
    platform_code: str
    quantity: int
    status: str
    retry_count: int
    message: str
    created_at: datetime

    @field_serializer("id", "shop_id", "sku_id")
    def _ids(self, value: int) -> str:
        return str(value)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        if value not in PUSH_STATUSES:
            raise ValueError("回传状态不支持")
        return value


class SafetyStockWrite(BaseModel):
    sku_id: int
    platform_code: str
    quantity: int = Field(ge=0, le=1_000_000)
    lead_time_days: int = Field(ge=0, le=3650)
    cover_days: int = Field(ge=0, le=3650)

    @field_validator("sku_id", mode="before")
    @classmethod
    def _sku(cls, value: object) -> int:
        return parse_id(value)

    @field_validator("platform_code")
    @classmethod
    def _platform(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("平台不能为空")
        return value.strip().lower()


class SafetyStockView(BaseModel):
    id: int
    sku_id: int
    sku_code: str
    platform_code: str
    quantity: int
    lead_time_days: int
    cover_days: int

    @field_serializer("id", "sku_id")
    def _ids(self, value: int) -> str:
        return str(value)


class ReplenishmentView(BaseModel):
    sku_id: int
    sku_code: str
    platform_code: str
    safety: int
    lead_time_days: int
    cover_days: int
    sold: int
    window_days: int
    movable: int
    in_transit: int
    daily_sales: Decimal
    suggested_qty: int
    order_on: date | None

    @field_serializer("sku_id")
    def _sku(self, value: int) -> str:
        return str(value)

    @field_serializer("daily_sales")
    def _daily(self, value: Decimal) -> str:
        return f"{value:.4f}"


class HoldView(BaseModel):
    order_item_id: int
    sku_id: int
    quantity: int
    short_qty: int
    status: str

    @field_serializer("order_item_id", "sku_id")
    def _ids(self, value: int) -> str:
        return str(value)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        if value not in HOLD_STATUSES:
            raise ValueError("预占状态不支持")
        return value


__all__ = [
    "FlowView",
    "HoldView",
    "InventoryAdjust",
    "InventorySafeStockPatch",
    "InventoryView",
    "PushLogView",
    "PushRequest",
    "ReplenishmentView",
    "SafetyStockView",
    "SafetyStockWrite",
    "WarehousePatch",
    "WarehouseView",
    "WarehouseWrite",
]
