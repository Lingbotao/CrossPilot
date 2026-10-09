"""落地成本引擎：十二项、印尼计税基础、缺规则不补 0、分摊与整数分。"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from app.engines.landed_cost import (
    CostLine,
    FeeFact,
    LandedCostInput,
    LandedCostResult,
    TaxFact,
    compute_landed_cost,
    quantize,
)

_DAY = date(2026, 10, 1)


def _tax(
    tax_type: str,
    rate: str,
    *,
    pattern: str = "*",
    numerator: int = 1,
    denominator: int = 1,
    threshold: str | None = None,
    threshold_currency: str | None = None,
    source: str = "税则",
) -> TaxFact:
    return TaxFact(
        tax_type=tax_type,
        hs_code_pattern=pattern,
        rate=Decimal(rate),
        basis_numerator=numerator,
        basis_denominator=denominator,
        threshold_amount=None if threshold is None else Decimal(threshold),
        threshold_currency=threshold_currency,
        source=source,
        verified_at=_DAY,
    )


def _fee(
    code: str,
    charge: str,
    amount: str,
    *,
    channel: str = "AIR",
    label: str = "*",
    currency: str | None = None,
    divisor: int | None = None,
    source: str = "费用台账",
) -> FeeFact:
    return FeeFact(
        fee_code=code,
        channel=channel,
        label=label,
        charge=charge,
        amount=Decimal(amount),
        currency=currency,
        volumetric_divisor=divisor,
        source=source,
        verified_at=_DAY,
    )


def _input(**overrides: object) -> LandedCostInput:
    base: dict[str, object] = {
        "market": "ID",
        "selling_currency": "USD",
        "channel": "AIR",
        "first_mile_method": "CHARGEABLE",
        "selling_price": Decimal("100"),
        "purchase_amount": Decimal("72"),
        "purchase_currency": "CNY",
        "fx_rate": Decimal("7.2"),
        "fx_source": "手工",
        "weight_g": Decimal("1000"),
        "volume_cm3": Decimal("1000"),
        "hs_code": "610910",
        "declared_value": Decimal("100"),
        "declared_currency": "USD",
        "storage_days": 0,
    }
    base.update(overrides)
    return LandedCostInput(**base)  # type: ignore[arg-type]


def _fees() -> list[FeeFact]:
    return [
        _fee("FIRST_MILE", "PER_KG", "3", currency="USD"),
        _fee("BROKERAGE", "FIXED", "1", label="报关费", currency="USD"),
        _fee("BROKERAGE", "FIXED", "1", label="MPF", currency="USD"),
        _fee("COMMISSION", "RATE", "0.15"),
        _fee("PAYMENT", "RATE", "0.03"),
        _fee("FULFILLMENT", "FIXED", "5", currency="USD"),
        _fee("ADS", "RATE", "0.10"),
        _fee("RETURN_RATE", "RATE", "0.05"),
        _fee("RETURN_LOSS", "RATE", "0.50"),
        _fee("FX_RESERVE", "RATE", "0.10"),
    ]


def _taxes() -> list[TaxFact]:
    return [
        _tax("DUTY", "0.10"),
        _tax("PPN", "0.11", numerator=11, denominator=12, source="印尼税则"),
    ]


def _line(result: LandedCostResult, code: str) -> CostLine:
    return next(line for line in result.lines if line.code == code)


def test_full_chain_uses_configured_basis_and_stays_on_decimal() -> None:
    result = compute_landed_cost(_input(), _taxes(), _fees())
    assert _line(result, "PURCHASE").amount == Decimal("10.000000")
    assert _line(result, "FIRST_MILE").amount == Decimal("3.000000")
    assert _line(result, "DUTY").amount == Decimal("10.000000")
    assert _line(result, "IMPORT_TAX").amount == quantize(
        (Decimal("100") + Decimal("10")) * Decimal(11) / Decimal(12) * Decimal("0.11")
    )
    assert "11/12" in _line(result, "IMPORT_TAX").formula
    assert "印尼税则" in _line(result, "IMPORT_TAX").source
    assert _line(result, "BROKERAGE").amount == Decimal("2.000000")
    assert _line(result, "STORAGE").amount == Decimal("0.000000")
    landed_before_fx = Decimal("10") + Decimal("3") + Decimal("10") + _line(result, "IMPORT_TAX").amount + Decimal("2")
    assert _line(result, "FX_RESERVE").amount == quantize(landed_before_fx * Decimal("0.10"))
    assert result.landed_cost == quantize(landed_before_fx + _line(result, "FX_RESERVE").amount)
    assert result.complete is True
    assert result.net_profit == quantize(
        Decimal("100")
        - result.landed_cost
        - Decimal("15")
        - Decimal("3")
        - Decimal("5")
        - Decimal("10")
        - Decimal("2.5")
    )
    assert result.profit_complete is True
    assert result.net_margin_percent == quantize(result.net_profit / Decimal("100") * Decimal("100"))
    invested = Decimal("10") + Decimal("3")
    assert result.roi_percent == quantize(result.net_profit / invested * Decimal("100"))


def test_missing_duty_keeps_landed_cost_empty() -> None:
    result = compute_landed_cost(_input(), [_tax("PPN", "0.11", numerator=11, denominator=12)], _fees())
    duty = _line(result, "DUTY")
    assert duty.complete is False
    assert duty.amount is None
    assert "未配置" in duty.formula
    assert result.landed_cost is None
    assert result.net_profit is None
    assert result.complete is False


def test_threshold_zeroes_duty_without_dropping_the_source() -> None:
    duty = _tax("DUTY", "0.10", threshold="80", threshold_currency="USD", source="低值门槛")
    result = compute_landed_cost(_input(declared_value=Decimal("40")), [duty, _taxes()[1]], _fees())
    line = _line(result, "DUTY")
    assert line.amount == Decimal("0.000000")
    assert line.complete is True
    assert "门槛" in line.formula
    assert "低值门槛" in line.source


def test_longest_hs_pattern_wins() -> None:
    taxes = [
        _tax("DUTY", "0.05"),
        _tax("DUTY", "0.20", pattern="6109", source="章"),
        _tax("PPN", "0.10"),
    ]
    result = compute_landed_cost(_input(storage_days=0), taxes, _fees())
    assert _line(result, "DUTY").amount == Decimal("20.000000")
    assert "章" in _line(result, "DUTY").source


def test_weight_allocation_splits_the_shipment() -> None:
    data = _input(
        first_mile_method="WEIGHT",
        shipment_cost=Decimal("30"),
        shipment_currency="USD",
        shipment_weight_g=Decimal("3000"),
        weight_g=Decimal("1000"),
    )
    result = compute_landed_cost(data, _taxes(), _fees())
    assert _line(result, "FIRST_MILE").amount == Decimal("10.000000")
    assert "整票" in _line(result, "FIRST_MILE").formula


def test_chargeable_weight_uses_the_configured_divisor() -> None:
    fees = [fee for fee in _fees() if fee.fee_code != "FIRST_MILE"]
    fees.append(_fee("FIRST_MILE", "PER_KG", "4", currency="USD", divisor=6000, source="货代价卡"))
    data = _input(weight_g=Decimal("500"), volume_cm3=Decimal("12000"))
    result = compute_landed_cost(data, _taxes(), fees)
    line = _line(result, "FIRST_MILE")
    assert line.amount == Decimal("8.000000")
    assert "6000" in line.formula
    assert "货代价卡" in line.source


def test_same_currency_purchase_does_not_need_a_rate() -> None:
    result = compute_landed_cost(
        _input(purchase_amount=Decimal("10"), purchase_currency="USD", fx_rate=None),
        _taxes(),
        _fees(),
    )
    assert _line(result, "PURCHASE").amount == Decimal("10.000000")


def test_brokerage_currency_mismatch_is_incomplete() -> None:
    fees = [fee for fee in _fees() if fee.fee_code != "BROKERAGE"]
    fees.append(_fee("BROKERAGE", "FIXED", "2", currency="CNY"))
    result = compute_landed_cost(_input(), _taxes(), fees)
    assert _line(result, "BROKERAGE").complete is False
    assert result.landed_cost is None


def test_other_channel_fee_does_not_apply() -> None:
    fees = [fee for fee in _fees() if fee.fee_code != "BROKERAGE"]
    fees.append(_fee("BROKERAGE", "FIXED", "2", channel="SEA_FCL", currency="USD"))
    result = compute_landed_cost(_input(), _taxes(), fees)
    assert _line(result, "BROKERAGE").complete is False
    assert "未配置" in _line(result, "BROKERAGE").formula


def test_engine_source_has_no_statutory_rate() -> None:
    source = (Path(__file__).parents[2] / "app" / "engines" / "landed_cost.py").read_text(encoding="utf-8")
    assert "float(" not in source
    assert "11/12" not in source
    assert "0.11" not in source
    assert "6000" not in source
