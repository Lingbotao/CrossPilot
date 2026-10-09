"""目标净利率反推与盈亏平衡。只调用落地成本引擎，不复制十二项公式。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from app.engines.landed_cost import (
    LINE_CODES,
    CostLine,
    FeeFact,
    LandedCostInput,
    LandedCostResult,
    TaxFact,
    compute_landed_cost,
    money_text,
    quantize,
)

_HUNDRED = Decimal("100")
_PROBE = (Decimal("1"), Decimal("2"), Decimal("4"))
_SLOPE_TOLERANCE = Decimal("0.000002")
_STEP = Decimal("5")
_EXTRA = Decimal("10")


@dataclass(frozen=True)
class PricePoint:
    target_margin_percent: Decimal
    selling_price: Decimal | None
    net_margin_percent: Decimal | None
    reachable: bool
    formula: str


@dataclass(frozen=True)
class PricingResult:
    suggested_price: Decimal | None
    break_even_price: Decimal | None
    break_even_quantity: Decimal | None
    reachable: bool
    formula: str
    quantity_formula: str
    currency: str
    curve: tuple[PricePoint, ...]
    verified: LandedCostResult | None
    gaps: tuple[str, ...]


def suggest_prices(
    data: LandedCostInput,
    taxes: tuple[TaxFact, ...] | list[TaxFact],
    fees: tuple[FeeFact, ...] | list[FeeFact],
    *,
    target_margin_percent: Decimal,
    period_fixed_cost: Decimal | None = None,
    disabled: frozenset[str] | set[str] | None = None,
) -> PricingResult:
    """用两个以上探针售价拆开固定成本与随售价变化的比例，再解目标净利率。"""
    off = frozenset(disabled or ())
    fixed, variable, gaps, sample = _decompose(data, taxes, fees, off)
    currency = data.selling_currency
    if fixed is None or variable is None:
        formula = "有成本项不完整，建议售价留空。"
        if gaps:
            formula = f"有成本项不完整（{'、'.join(gaps)}），建议售价留空。"
        return PricingResult(
            suggested_price=None,
            break_even_price=None,
            break_even_quantity=None,
            reachable=False,
            formula=formula,
            quantity_formula="成本不完整，保本销量留空。",
            currency=currency,
            curve=(),
            verified=sample,
            gaps=gaps,
        )
    points = tuple(_curve_percents(target_margin_percent))
    curve = tuple(_quote(data, taxes, fees, off, fixed, variable, percent, currency) for percent in points)
    suggested = next((point for point in curve if point.target_margin_percent == target_margin_percent), curve[-1])
    break_even = _quote(data, taxes, fees, off, fixed, variable, Decimal("0"), currency)
    quantity, quantity_formula = _quantity(
        data,
        taxes,
        fees,
        off,
        suggested.selling_price,
        period_fixed_cost,
        currency,
    )
    verified = None
    if suggested.selling_price is not None:
        verified = compute_landed_cost(
            replace(data, selling_price=suggested.selling_price),
            taxes,
            fees,
            disabled=off,
        )
    return PricingResult(
        suggested_price=suggested.selling_price,
        break_even_price=break_even.selling_price,
        break_even_quantity=quantity,
        reachable=suggested.reachable,
        formula=suggested.formula,
        quantity_formula=quantity_formula,
        currency=currency,
        curve=curve,
        verified=verified,
        gaps=(),
    )


def _curve_percents(target: Decimal) -> list[Decimal]:
    end = target + _EXTRA
    points: list[Decimal] = []
    current = Decimal("0")
    while current <= end:
        points.append(current)
        current += _STEP
    if target not in points:
        points.append(target)
    if end not in points:
        points.append(end)
    return sorted(points)


def _decompose(
    data: LandedCostInput,
    taxes: tuple[TaxFact, ...] | list[TaxFact],
    fees: tuple[FeeFact, ...] | list[FeeFact],
    disabled: frozenset[str],
) -> tuple[Decimal | None, Decimal | None, tuple[str, ...], LandedCostResult]:
    runs = [compute_landed_cost(replace(data, selling_price=price), taxes, fees, disabled=disabled) for price in _PROBE]
    gaps: list[str] = []
    for code in LINE_CODES:
        if any(not _line(run, code).complete or _line(run, code).amount is None for run in runs):
            gaps.append(code)
    if gaps:
        return None, None, tuple(gaps), runs[0]
    fixed = Decimal("0")
    variable = Decimal("0")
    for code in LINE_CODES:
        amounts = [(_line(run, code).amount or Decimal("0")) for run in runs]
        slope_ab = (amounts[1] - amounts[0]) / (_PROBE[1] - _PROBE[0])
        slope_bc = (amounts[2] - amounts[1]) / (_PROBE[2] - _PROBE[1])
        if abs(slope_ab - slope_bc) > _SLOPE_TOLERANCE:
            return None, None, ("NON_LINEAR",), runs[0]
        fixed += amounts[0] - slope_ab * _PROBE[0]
        variable += slope_ab
    return fixed, variable, (), runs[0]


def _quote(
    data: LandedCostInput,
    taxes: tuple[TaxFact, ...] | list[TaxFact],
    fees: tuple[FeeFact, ...] | list[FeeFact],
    disabled: frozenset[str],
    fixed: Decimal,
    variable: Decimal,
    target_percent: Decimal,
    currency: str,
) -> PricePoint:
    margin = target_percent / _HUNDRED
    if fixed <= 0:
        constant = quantize((Decimal("1") - variable) * _HUNDRED)
        return PricePoint(
            target_percent,
            None,
            None,
            False,
            f"没有与售价无关的成本，净利率恒为 {money_text(constant)}%，不存在唯一建议售价。",
        )
    denominator = Decimal("1") - variable - margin
    if denominator <= 0:
        return PricePoint(
            target_percent,
            None,
            None,
            False,
            (
                f"目标净利率 {money_text(target_percent)}% 加上随售价变化的成本比例"
                f" {money_text(variable)} 已经不少于 100%，没有能达到该净利率的售价。"
            ),
        )
    price = quantize(fixed / denominator)
    price, checked = _match_margin(data, taxes, fees, disabled, price, target_percent)
    formula = (
        f"建议售价 = 固定成本 {money_text(fixed)} / (1 - 比例系数 {money_text(variable)}"
        f" - 目标净利率 {money_text(margin)}) = {money_text(price)} {currency}。"
    )
    return PricePoint(target_percent, price, checked.net_margin_percent, checked.profit_complete, formula)


def _match_margin(
    data: LandedCostInput,
    taxes: tuple[TaxFact, ...] | list[TaxFact],
    fees: tuple[FeeFact, ...] | list[FeeFact],
    disabled: frozenset[str],
    price: Decimal,
    target_percent: Decimal,
) -> tuple[Decimal, LandedCostResult]:
    """售价按 6 位四舍五入后，把净利率对齐到目标的最后一位。"""
    target = quantize(target_percent)
    step = Decimal("0.000001")
    best_price = price
    best = compute_landed_cost(replace(data, selling_price=price), taxes, fees, disabled=disabled)
    best_gap = None if best.net_margin_percent is None else abs(best.net_margin_percent - target)
    for candidate in (quantize(price - step), price, quantize(price + step)):
        if candidate <= 0:
            continue
        checked = compute_landed_cost(replace(data, selling_price=candidate), taxes, fees, disabled=disabled)
        if checked.net_margin_percent is None:
            continue
        gap = abs(checked.net_margin_percent - target)
        if best_gap is None or gap < best_gap:
            best_gap = gap
            best_price = candidate
            best = checked
    return best_price, best


def _quantity(
    data: LandedCostInput,
    taxes: tuple[TaxFact, ...] | list[TaxFact],
    fees: tuple[FeeFact, ...] | list[FeeFact],
    disabled: frozenset[str],
    suggested: Decimal | None,
    period_fixed_cost: Decimal | None,
    currency: str,
) -> tuple[Decimal | None, str]:
    if period_fixed_cost is None:
        return None, "未填写期间固定成本，只给出保本售价。"
    reference = data.selling_price if data.selling_price is not None and data.selling_price > 0 else suggested
    if reference is None or reference <= 0:
        return None, "没有可用售价，保本销量留空。"
    unit = compute_landed_cost(replace(data, selling_price=reference), taxes, fees, disabled=disabled)
    if unit.net_profit is None or unit.net_profit <= 0:
        return None, "单位净利不是正数，无法用销量覆盖期间固定成本。"
    quantity = quantize(period_fixed_cost / unit.net_profit)
    return quantity, (
        f"保本销量 = 期间固定成本 {money_text(period_fixed_cost)} {currency}"
        f" / 单位净利 {money_text(unit.net_profit)} = {money_text(quantity)}。"
    )


def _line(result: LandedCostResult, code: str) -> CostLine:
    return next(line for line in result.lines if line.code == code)
