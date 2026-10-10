"""采购状态机、收货数量和头程分摊。纯函数，不访问数据库。

到仓成本仍走落地成本引擎。这里只决定分摊金额、尾差和加权平均，不另造利润口径。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.engines.landed_cost import LANDED_CODES, CostLine, LandedCostResult, money_text, quantize

PO_STATUSES: tuple[str, ...] = (
    "DRAFT",
    "PENDING",
    "APPROVED",
    "PARTIAL",
    "RECEIVED",
    "CLOSED",
    "CANCELLED",
)
RECEIPT_DISPOSITIONS: tuple[str, ...] = ("RECEIVE", "SHORT")
ALLOC_METHODS: tuple[str, ...] = ("WEIGHT", "VOLUME", "VALUE")
SETTLEMENT_TYPES: tuple[str, ...] = ("PREPAY", "CREDIT", "COD")
SHIPMENT_STATUSES: tuple[str, ...] = ("DRAFT", "POSTED")
TRANSIT_DOMESTIC = "DOMESTIC"
TRANSIT_OVERSEAS = "OVERSEAS"
OPEN_FOR_RECEIPT = frozenset({"APPROVED", "PARTIAL"})
OPEN_FOR_EDIT = frozenset({"DRAFT", "PENDING", "APPROVED", "PARTIAL"})
STOCK_POSTED = frozenset({"APPROVED", "PARTIAL", "RECEIVED"})


class PurchaseRuleError(Exception):
    """状态或数量不合法。``code`` 由服务层映射到 60008 / 60009。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def open_qty(ordered: int, received: int, short: int) -> int:
    return max(0, ordered - received - short)


def assert_transition(status: str, action: str, *, received: int) -> str:
    """返回动作之后的状态。取消已收货的单会被拒绝。"""

    if action == "submit" and status == "DRAFT":
        return "PENDING"
    if action == "reject" and status == "PENDING":
        return "DRAFT"
    if action == "approve" and status == "PENDING":
        return "APPROVED"
    if action == "cancel" and status in {"DRAFT", "PENDING"}:
        return "CANCELLED"
    if action == "cancel" and status == "APPROVED":
        if received > 0:
            raise PurchaseRuleError("state", "已经收过货，不能取消")
        return "CANCELLED"
    if action == "close" and status in {"PARTIAL", "RECEIVED"}:
        return "CLOSED"
    raise PurchaseRuleError("state", "采购单当前状态不能执行该操作")


def assert_can_edit(status: str) -> None:
    if status not in OPEN_FOR_EDIT:
        raise PurchaseRuleError("state", "采购单当前状态不能变更")


def assert_can_receive(status: str) -> None:
    if status not in OPEN_FOR_RECEIPT:
        raise PurchaseRuleError("state", "采购单当前状态不能收货")


def transit_delta_for_qty_change(
    status: str,
    ordered: int,
    received: int,
    short: int,
    new_ordered: int,
) -> int:
    if new_ordered <= 0:
        raise PurchaseRuleError("receipt", "数量必须大于 0")
    if new_ordered < received + short:
        raise PurchaseRuleError("receipt", "数量不能小于已收加短收")
    assert_can_edit(status)
    if status in {"DRAFT", "PENDING"}:
        return 0
    return open_qty(new_ordered, received, short) - open_qty(ordered, received, short)


@dataclass(frozen=True)
class ReceiptPlan:
    receive_qty: int
    transit_release: int
    new_received: int
    new_short: int


def plan_receive(
    *,
    ordered: int,
    received: int,
    short: int,
    quantity: int,
    disposition: str,
    allow_over: bool,
) -> ReceiptPlan:
    if disposition not in RECEIPT_DISPOSITIONS:
        raise PurchaseRuleError("receipt", "收货处理方式不支持")
    remaining = open_qty(ordered, received, short)
    if disposition == "SHORT":
        if remaining <= 0:
            raise PurchaseRuleError("receipt", "没有剩余数量可以短收")
        return ReceiptPlan(0, remaining, received, short + remaining)
    if quantity <= 0:
        raise PurchaseRuleError("receipt", "收货数量必须大于 0")
    if quantity > remaining and not allow_over:
        raise PurchaseRuleError("receipt", "超收需要明确确认")
    return ReceiptPlan(quantity, min(quantity, remaining), received + quantity, short)


def next_receipt_status(lines: list[tuple[int, int, int]]) -> str:
    """每行是订购、已收、短收。全部结清为已收货，否则部分收货。"""

    if lines and all(open_qty(ordered, received, short) == 0 for ordered, received, short in lines):
        return "RECEIVED"
    if any(received or short for _, received, short in lines):
        return "PARTIAL"
    return "APPROVED"


def transit_region(warehouse_type: str) -> str:
    if warehouse_type == "LOCAL":
        return TRANSIT_DOMESTIC
    if warehouse_type in {"OVERSEAS", "FBA"}:
        return TRANSIT_OVERSEAS
    raise PurchaseRuleError("state", "平台仓不能作为采购或头程目的仓")


def should_move_stock(*, from_id: int | None, to_id: int | None, po_warehouse_type: str | None) -> bool:
    """海外目的的采购单不再把头程库存搬一次，避免在途记两遍。"""

    if from_id is None or to_id is None or from_id == to_id:
        return False
    return po_warehouse_type not in {"OVERSEAS", "FBA"}


@dataclass(frozen=True)
class AllocInput:
    sku_id: int
    quantity: int
    weight_g: Decimal
    volume_cm3: Decimal
    unit_value: Decimal


@dataclass(frozen=True)
class AllocOutput:
    sku_id: int
    quantity: int
    allocated_cost: Decimal
    unit_cost: Decimal
    formula: str
    source: str


def allocate(method: str, total: Decimal, currency: str, lines: list[AllocInput]) -> list[AllocOutput]:
    if method not in ALLOC_METHODS:
        raise PurchaseRuleError("receipt", "头程分摊方式不支持")
    if total <= 0:
        raise PurchaseRuleError("receipt", "头程总额必须大于 0")
    if not lines:
        raise PurchaseRuleError("receipt", "头程单至少要有一行")
    metrics = [_metric(method, line) for line in lines]
    whole = sum(metrics, Decimal("0"))
    if whole <= 0:
        raise PurchaseRuleError("receipt", "分摊基数必须大于 0")
    name = {"WEIGHT": "重量", "VOLUME": "体积", "VALUE": "货值"}[method]
    shares = [quantize(total * metric / whole) for metric in metrics]
    remainder = quantize(total - sum(shares, Decimal("0")))
    winner = max(range(len(lines)), key=lambda index: (metrics[index], -index))
    if remainder != 0:
        shares[winner] = quantize(shares[winner] + remainder)
    source = "头程物流单"
    outputs: list[AllocOutput] = []
    for index, line in enumerate(lines):
        share = shares[index]
        unit = quantize(share / Decimal(line.quantity))
        formula = (
            f"头程分摊 = 总额 {money_text(total)} × 本行{name} {money_text(metrics[index])}"
            f" / 整票{name} {money_text(whole)} = {money_text(share)} {currency}。"
        )
        if index == winner and remainder != 0:
            formula += f"尾差 {money_text(remainder)} 计入本行。"
        formula += f"单位头程 = {money_text(share)} / {line.quantity} = {money_text(unit)} {currency}。"
        outputs.append(
            AllocOutput(
                sku_id=line.sku_id,
                quantity=line.quantity,
                allocated_cost=share,
                unit_cost=unit,
                formula=formula,
                source=source,
            )
        )
    return outputs


def _metric(method: str, line: AllocInput) -> Decimal:
    if line.quantity <= 0:
        raise PurchaseRuleError("receipt", "头程数量必须大于 0")
    if method == "WEIGHT":
        unit = line.weight_g
        label = "重量"
    elif method == "VOLUME":
        unit = line.volume_cm3
        label = "体积"
    else:
        unit = line.unit_value
        label = "货值"
    if unit <= 0:
        raise PurchaseRuleError("receipt", f"按{label}分摊时，{label}必须大于 0")
    return unit * Decimal(line.quantity)


def bind_first_mile(result: LandedCostResult, formula: str, source: str) -> LandedCostResult:
    """把头程行的说明换成真实分摊式。金额不变，汇兑预备金仍按这笔头程计算。"""

    lines: list[CostLine] = []
    for line in result.lines:
        if line.code == "FIRST_MILE" and line.complete and line.amount is not None:
            lines.append(CostLine(line.code, line.label, line.amount, line.currency, formula, source, True))
        else:
            lines.append(line)
    landed_parts = [line for line in lines if line.code in LANDED_CODES]
    if any(not line.complete or line.amount is None for line in landed_parts):
        return LandedCostResult(
            lines=tuple(lines),
            currency=result.currency,
            landed_cost=None,
            net_profit=None,
            net_margin_percent=None,
            roi_percent=None,
            complete=False,
            profit_complete=False,
        )
    landed = quantize(sum((line.amount for line in landed_parts if line.amount is not None), Decimal("0")))
    return LandedCostResult(
        lines=tuple(lines),
        currency=result.currency,
        landed_cost=landed,
        net_profit=None,
        net_margin_percent=None,
        roi_percent=None,
        complete=True,
        profit_complete=False,
    )


def incomplete_landed(result: LandedCostResult) -> list[str]:
    return [line.code for line in result.lines if line.code in LANDED_CODES and not line.complete]


def weighted_unit(old_qty: int, old_unit: Decimal, new_qty: int, new_unit: Decimal) -> Decimal:
    if new_qty <= 0:
        raise PurchaseRuleError("receipt", "到仓数量必须大于 0")
    total_qty = old_qty + new_qty
    if old_qty <= 0:
        return quantize(new_unit)
    return quantize((Decimal(old_qty) * old_unit + Decimal(new_qty) * new_unit) / Decimal(total_qty))


def average_formula(
    label: str,
    old_qty: int,
    old_unit: Decimal,
    new_qty: int,
    new_unit: Decimal,
    averaged: Decimal,
    currency: str,
) -> str:
    if old_qty <= 0:
        return f"{label} = 本次到仓 {money_text(new_unit)} {currency}。"
    return (
        f"{label} = ({old_qty} × {money_text(old_unit)} + {new_qty} × {money_text(new_unit)})"
        f" / {old_qty + new_qty} = {money_text(averaged)} {currency}。"
    )
