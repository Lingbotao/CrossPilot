"""结算明细与订单的匹配。只做金额比较，不折算币种，也不改利润。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.engines.landed_cost import quantize

MATCHED = "MATCHED"
UNMATCHED = "UNMATCHED"
NOTE_UNMATCHED = "未匹配到订单"
NOTE_CURRENCY = "币种不一致，未折算"
NOTE_COMPARED = "同币种差额 = 结算合计 − 订单总额"


@dataclass(frozen=True)
class OrderRef:
    order_id: int
    total_amount: Decimal
    currency: str


@dataclass(frozen=True)
class SettlementLine:
    platform_order_id: str
    fee_type: str
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class MatchedLine:
    platform_order_id: str
    fee_type: str
    amount: Decimal
    currency: str
    order_id: int | None
    status: str


@dataclass(frozen=True)
class OrderGap:
    platform_order_id: str
    order_id: int | None
    order_amount: Decimal | None
    order_currency: str | None
    settlement_amount: Decimal | None
    settlement_currency: str | None
    deviation: Decimal | None
    note: str


def reconcile(
    lines: tuple[SettlementLine, ...] | list[SettlementLine],
    orders: dict[str, OrderRef],
) -> tuple[tuple[MatchedLine, ...], tuple[OrderGap, ...], Decimal]:
    """返回明细、按订单汇总的差额，以及已匹配行数 / 总行数。"""

    matched: list[MatchedLine] = []
    grouped: dict[str, list[SettlementLine]] = {}
    hits = 0
    for line in lines:
        order = orders.get(line.platform_order_id)
        if order is None:
            status = UNMATCHED
            order_id = None
        else:
            status = MATCHED
            order_id = order.order_id
            hits += 1
        matched.append(
            MatchedLine(
                platform_order_id=line.platform_order_id,
                fee_type=line.fee_type,
                amount=quantize(line.amount),
                currency=line.currency,
                order_id=order_id,
                status=status,
            )
        )
        grouped.setdefault(line.platform_order_id, []).append(line)
    gaps = tuple(
        _gap(platform_order_id, group, orders.get(platform_order_id)) for platform_order_id, group in grouped.items()
    )
    total = len(lines)
    rate = Decimal(0) if total == 0 else quantize(Decimal(hits) / Decimal(total))
    return tuple(matched), gaps, rate


def header_amount(lines: tuple[SettlementLine, ...] | list[SettlementLine], currency: str) -> Decimal:
    total = sum((line.amount for line in lines if line.currency == currency), Decimal(0))
    return quantize(total)


def _gap(platform_order_id: str, lines: list[SettlementLine], order: OrderRef | None) -> OrderGap:
    currencies = {line.currency for line in lines}
    settlement_currency = next(iter(currencies)) if len(currencies) == 1 else None
    settlement_amount = quantize(sum((line.amount for line in lines), Decimal(0))) if settlement_currency else None
    if order is None:
        return OrderGap(
            platform_order_id,
            None,
            None,
            None,
            settlement_amount,
            settlement_currency,
            None,
            NOTE_UNMATCHED,
        )
    if settlement_currency != order.currency:
        return OrderGap(
            platform_order_id,
            order.order_id,
            quantize(order.total_amount),
            order.currency,
            settlement_amount,
            settlement_currency,
            None,
            NOTE_CURRENCY,
        )
    assert settlement_amount is not None
    return OrderGap(
        platform_order_id,
        order.order_id,
        quantize(order.total_amount),
        order.currency,
        settlement_amount,
        settlement_currency,
        quantize(settlement_amount - order.total_amount),
        NOTE_COMPARED,
    )
