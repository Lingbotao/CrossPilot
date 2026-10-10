"""平台结算单。金额是十进制字符串，偏差在币种不一致时留空。"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, field_serializer


def _id_text(value: int) -> str:
    return str(value)


class SettlementSummary(BaseModel):
    id: int
    shop_id: int
    platform_settlement_id: str
    period_start: date
    period_end: date
    amount: str
    currency: str
    source: str
    line_count: int
    matched_count: int
    match_rate: str

    @field_serializer("id", "shop_id")
    def _ids(self, value: int) -> str:
        return _id_text(value)


class SettlementItemView(BaseModel):
    id: int
    platform_order_id: str
    order_id: int | None
    fee_type: str
    amount: str
    currency: str
    match_status: str

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return _id_text(value)

    @field_serializer("order_id")
    def _order(self, value: int | None) -> str | None:
        if value is None:
            return None
        return _id_text(value)


class OrderGapView(BaseModel):
    platform_order_id: str
    order_id: int | None
    order_amount: str | None
    order_currency: str | None
    settlement_amount: str | None
    settlement_currency: str | None
    deviation: str | None
    note: str

    @field_serializer("order_id")
    def _order(self, value: int | None) -> str | None:
        if value is None:
            return None
        return _id_text(value)


class SettlementDetail(SettlementSummary):
    items: list[SettlementItemView]
    gaps: list[OrderGapView]
