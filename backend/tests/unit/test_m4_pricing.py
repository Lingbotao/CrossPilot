"""反推售价、成本项开关、汇兑损益与日利润汇总。"""

from datetime import date
from decimal import Decimal

from app.engines.landed_cost import (
    FeeFact,
    LandedCostInput,
    TaxFact,
    compute_landed_cost,
    quantize,
)
from app.engines.pricing import suggest_prices
from app.engines.profit import build_waterfall, fx_gain_loss, rollup_daily

_DAY = date(2026, 10, 1)


def _fee(code: str, charge: str, amount: str, *, currency: str | None = None) -> FeeFact:
    return FeeFact(
        fee_code=code,
        channel="AIR",
        label="*",
        charge=charge,
        amount=Decimal(amount),
        currency=currency,
        volumetric_divisor=None,
        source="费用台账",
        verified_at=_DAY,
    )


def _tax(tax_type: str, rate: str) -> TaxFact:
    return TaxFact(
        tax_type=tax_type,
        hs_code_pattern="*",
        rate=Decimal(rate),
        basis_numerator=1,
        basis_denominator=1,
        threshold_amount=None,
        threshold_currency=None,
        source="税则",
        verified_at=_DAY,
    )


def _input(**overrides: object) -> LandedCostInput:
    base: dict[str, object] = {
        "market": "US",
        "selling_currency": "USD",
        "channel": "AIR",
        "first_mile_method": "CHARGEABLE",
        "selling_price": Decimal("100"),
        "purchase_amount": Decimal("40"),
        "purchase_currency": "USD",
        "weight_g": Decimal("500"),
        "volume_cm3": Decimal("1000"),
        "hs_code": "610910",
        "declared_value": Decimal("20"),
        "declared_currency": "USD",
        "storage_days": 0,
    }
    base.update(overrides)
    return LandedCostInput(**base)  # type: ignore[arg-type]


def _fees() -> list[FeeFact]:
    return [
        _fee("COMMISSION", "RATE", "0.10"),
        _fee("PAYMENT", "RATE", "0"),
        _fee("FULFILLMENT", "FIXED", "0", currency="USD"),
        _fee("ADS", "RATE", "0"),
        _fee("RETURN_RATE", "RATE", "0"),
        _fee("RETURN_LOSS", "RATE", "0"),
    ]


_OFF = frozenset({"FIRST_MILE", "DUTY", "IMPORT_TAX", "BROKERAGE", "STORAGE", "FX_RESERVE"})


def test_suggested_price_round_trips_to_the_target_margin() -> None:
    fees = [fee for fee in _fees() if fee.fee_code != "COMMISSION"]
    fees.append(_fee("COMMISSION", "RATE", "0.20"))
    priced = suggest_prices(
        _input(selling_price=None, purchase_amount=Decimal("30")),
        [],
        fees,
        target_margin_percent=Decimal("20"),
        disabled=_OFF,
    )
    assert priced.reachable is True
    assert priced.suggested_price == Decimal("50.000000")
    assert priced.verified is not None
    assert priced.verified.net_margin_percent == Decimal("20.000000")
    assert priced.break_even_price == Decimal("37.500000")
    assert any(point.target_margin_percent == Decimal("20") for point in priced.curve)
    assert any(point.target_margin_percent == Decimal("30") for point in priced.curve)


def test_high_variable_rate_has_no_price() -> None:
    fees = [fee for fee in _fees() if fee.fee_code != "COMMISSION"]
    fees.append(_fee("COMMISSION", "RATE", "0.90"))
    priced = suggest_prices(
        _input(selling_price=None),
        [],
        fees,
        target_margin_percent=Decimal("20"),
        disabled=_OFF,
    )
    assert priced.reachable is False
    assert priced.suggested_price is None
    assert "不少于 100%" in priced.formula


def test_missing_rule_leaves_the_suggestion_empty() -> None:
    priced = suggest_prices(_input(selling_price=None), [], _fees(), target_margin_percent=Decimal("20"))
    assert priced.suggested_price is None
    assert "DUTY" in priced.gaps


def test_disabled_line_is_zero_and_complete() -> None:
    result = compute_landed_cost(
        _input(),
        [],
        _fees(),
        disabled=frozenset({"FIRST_MILE", "DUTY", "IMPORT_TAX", "BROKERAGE", "STORAGE", "FX_RESERVE", "ADS"}),
    )
    ads = next(line for line in result.lines if line.code == "ADS")
    assert ads.amount == Decimal("0.000000")
    assert ads.complete is True
    assert "已关闭" in ads.formula
    assert result.landed_cost is not None


def test_break_even_quantity_uses_period_fixed_cost() -> None:
    priced = suggest_prices(
        _input(selling_price=Decimal("80")),
        [],
        _fees(),
        target_margin_percent=Decimal("20"),
        period_fixed_cost=Decimal("320"),
        disabled=_OFF,
    )
    assert priced.break_even_quantity == Decimal("10.000000")
    assert "期间固定成本" in priced.quantity_formula


def test_blank_period_cost_does_not_invent_a_quantity() -> None:
    priced = suggest_prices(
        _input(selling_price=Decimal("80")),
        [],
        _fees(),
        target_margin_percent=Decimal("20"),
        disabled=_OFF,
    )
    assert priced.break_even_quantity is None
    assert "未填写期间固定成本" in priced.quantity_formula


def test_fx_gain_is_positive_and_excluded_from_profit() -> None:
    gain = fx_gain_loss(
        foreign_amount=Decimal("10"),
        order_rate=Decimal("7"),
        settlement_rate=Decimal("7.2"),
        order_source="下单台账",
        settlement_source="结算台账",
        book_currency="CNY",
    )
    assert gain.amount == Decimal("2.000000")
    assert gain.complete is True
    assert "不计入商品成本" in gain.formula
    result = compute_landed_cost(_input(), [], _fees(), disabled=_OFF)
    rolled = rollup_daily([(2, Decimal("100"), result)], book_rate=Decimal("7"), book_currency="CNY", fx=gain)
    assert rolled.revenue == Decimal("200.000000")
    assert rolled.net_profit == quantize((result.net_profit or Decimal("0")) * 2)
    assert rolled.fx_gain == Decimal("2.000000")
    assert rolled.book_net_profit == quantize((rolled.net_profit or Decimal("0")) * Decimal("7"))
    steps = build_waterfall(
        revenue=rolled.revenue,
        lines=rolled.lines,
        net_profit=rolled.net_profit,
        fx_gain=rolled.fx_gain,
        fx_formula=rolled.fx_formula,
    )
    assert steps[-1].memo is True
    assert steps[-1].code == "FX_GAIN"
    assert steps[-1].running == rolled.net_profit


def test_missing_settlement_rate_leaves_fx_empty() -> None:
    gain = fx_gain_loss(
        foreign_amount=Decimal("10"),
        order_rate=Decimal("7"),
        settlement_rate=None,
        order_source="下单台账",
        settlement_source="未配置",
        book_currency="CNY",
    )
    assert gain.amount is None
    assert gain.complete is False


def test_same_currency_fx_is_zero() -> None:
    gain = fx_gain_loss(
        foreign_amount=Decimal("10"),
        order_rate=None,
        settlement_rate=None,
        order_source="",
        settlement_source="",
        book_currency="USD",
        same_currency=True,
    )
    assert gain.amount == Decimal("0")
    assert "不计入商品成本" in gain.formula
