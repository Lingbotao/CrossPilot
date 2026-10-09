"""Landed Cost 纯函数。

Landed Cost = 采购成本 + 头程物流费 + 进口关税 + 进口环节税费
            + 报关清关固定费 + 仓储费 + 汇兑预备金
净利 = 售价 - Landed Cost - 平台佣金 - 支付手续费 - 平台物流费 - 广告费 - 退货损失摊销
净利率 = 净利 / 售价 × 100%
ROI   = 净利 / (采购成本 + 头程物流分摊) × 100%

税率、费率、固定费和体积重除数都由调用方传入。这里不写法定数字，也不访问数据库。
缺规则的项目标为不完整，不用 0 顶上。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

_SCALE = Decimal("0.000001")
_HUNDRED = Decimal("100")
_GRAMS_PER_KG = Decimal("1000")
_CM3_PER_CBM = Decimal("1000000")

CHANNELS: tuple[str, ...] = ("AIR", "SEA_FCL", "SEA_LCL", "EXPRESS", "PACKET", "*")
FEE_CODES: tuple[str, ...] = (
    "FIRST_MILE",
    "BROKERAGE",
    "COMMISSION",
    "PAYMENT",
    "FULFILLMENT",
    "ADS",
    "RETURN_RATE",
    "RETURN_LOSS",
    "STORAGE",
    "FX_RESERVE",
)
CHARGES: tuple[str, ...] = ("RATE", "FIXED", "PER_KG", "PER_CBM", "PER_CBM_DAY")
FIRST_MILE_METHODS: tuple[str, ...] = ("WEIGHT", "VOLUME", "VALUE", "CHARGEABLE")
IMPORT_TAX_TYPES: tuple[str, ...] = ("VAT", "GST", "SST", "PPN", "SALES_TAX")
LANDED_CODES: tuple[str, ...] = (
    "PURCHASE",
    "FIRST_MILE",
    "DUTY",
    "IMPORT_TAX",
    "BROKERAGE",
    "STORAGE",
    "FX_RESERVE",
)
DEDUCTION_CODES: tuple[str, ...] = ("COMMISSION", "PAYMENT", "FULFILLMENT", "ADS", "RETURN")
LINE_CODES: tuple[str, ...] = (
    "PURCHASE",
    "FIRST_MILE",
    "DUTY",
    "IMPORT_TAX",
    "BROKERAGE",
    "STORAGE",
    "FX_RESERVE",
    "COMMISSION",
    "PAYMENT",
    "FULFILLMENT",
    "ADS",
    "RETURN",
)
LINE_LABELS: dict[str, str] = {
    "PURCHASE": "采购成本",
    "FIRST_MILE": "头程物流费",
    "DUTY": "进口关税",
    "IMPORT_TAX": "进口环节税费",
    "BROKERAGE": "报关清关固定费",
    "STORAGE": "仓储费",
    "FX_RESERVE": "汇兑预备金",
    "COMMISSION": "平台佣金",
    "PAYMENT": "支付手续费",
    "FULFILLMENT": "平台物流费",
    "ADS": "广告费",
    "RETURN": "退货损失摊销",
}


@dataclass(frozen=True)
class TaxFact:
    tax_type: str
    hs_code_pattern: str
    rate: Decimal
    basis_numerator: int
    basis_denominator: int
    threshold_amount: Decimal | None
    threshold_currency: str | None
    source: str
    verified_at: date | None = None


@dataclass(frozen=True)
class FeeFact:
    fee_code: str
    channel: str
    label: str
    charge: str
    amount: Decimal
    currency: str | None
    volumetric_divisor: int | None
    source: str
    verified_at: date | None = None


@dataclass(frozen=True)
class LandedCostInput:
    market: str
    selling_currency: str
    channel: str
    first_mile_method: str
    selling_price: Decimal | None = None
    purchase_amount: Decimal | None = None
    purchase_currency: str | None = None
    fx_rate: Decimal | None = None
    fx_source: str = ""
    weight_g: Decimal | None = None
    volume_cm3: Decimal | None = None
    hs_code: str = ""
    declared_value: Decimal | None = None
    declared_currency: str | None = None
    shipment_cost: Decimal | None = None
    shipment_currency: str | None = None
    shipment_weight_g: Decimal | None = None
    shipment_volume_cm3: Decimal | None = None
    shipment_value: Decimal | None = None
    storage_days: int | None = None


@dataclass(frozen=True)
class CostLine:
    code: str
    label: str
    amount: Decimal | None
    currency: str
    formula: str
    source: str
    complete: bool


@dataclass(frozen=True)
class LandedCostResult:
    lines: tuple[CostLine, ...]
    currency: str
    landed_cost: Decimal | None
    net_profit: Decimal | None
    net_margin_percent: Decimal | None
    roi_percent: Decimal | None
    complete: bool
    profit_complete: bool


def quantize(value: Decimal) -> Decimal:
    return value.quantize(_SCALE, rounding=ROUND_HALF_UP)


def money_text(value: Decimal) -> str:
    return f"{quantize(value):.6f}"


def compute_landed_cost(
    data: LandedCostInput,
    taxes: tuple[TaxFact, ...] | list[TaxFact],
    fees: tuple[FeeFact, ...] | list[FeeFact],
    disabled: frozenset[str] | set[str] | None = None,
) -> LandedCostResult:
    currency = data.selling_currency
    off = frozenset(disabled or ())

    def gate(line: CostLine) -> CostLine:
        if line.code not in off:
            return line
        return CostLine(line.code, LINE_LABELS[line.code], Decimal("0"), currency, "本项已关闭。", "租户配置", True)

    purchase = gate(_purchase(data, currency))
    duty = gate(_duty(data, taxes, currency))
    import_tax = gate(_import_tax(data, taxes, currency, duty))
    brokerage = gate(_brokerage(data, fees, currency))
    storage = gate(_storage(data, fees, currency))
    first_mile = gate(_first_mile(data, fees, currency, purchase))
    fx_reserve = gate(
        _fx_reserve(fees, data.channel, currency, (purchase, first_mile, duty, import_tax, brokerage, storage))
    )
    price_lines = tuple(gate(line) for line in _price_lines(data, fees, currency))
    ordered = (purchase, first_mile, duty, import_tax, brokerage, storage, fx_reserve, *price_lines)
    landed_parts = [line for line in ordered if line.code in LANDED_CODES]
    deduction_parts = [line for line in ordered if line.code in DEDUCTION_CODES]
    landed = _total(landed_parts)
    profit_ready = landed is not None and all(line.complete for line in deduction_parts)
    net: Decimal | None = None
    margin: Decimal | None = None
    roi: Decimal | None = None
    if profit_ready and data.selling_price is not None and landed is not None:
        deducted = _total(deduction_parts)
        if deducted is not None:
            net = quantize(data.selling_price - landed - deducted)
            if data.selling_price > 0:
                margin = quantize(net / data.selling_price * _HUNDRED)
            base = _invested(purchase, first_mile)
            if base is not None and base > 0:
                roi = quantize(net / base * _HUNDRED)
    return LandedCostResult(
        lines=tuple(ordered),
        currency=currency,
        landed_cost=landed,
        net_profit=net,
        net_margin_percent=margin,
        roi_percent=roi,
        complete=landed is not None,
        profit_complete=net is not None,
    )


def _gap(code: str, currency: str, formula: str, source: str = "未配置") -> CostLine:
    return CostLine(code, LINE_LABELS[code], None, currency, formula, source, False)


def _ok(code: str, amount: Decimal, currency: str, formula: str, source: str) -> CostLine:
    return CostLine(code, LINE_LABELS[code], quantize(amount), currency, formula, source, True)


def _total(lines: list[CostLine] | tuple[CostLine, ...]) -> Decimal | None:
    if any(not line.complete or line.amount is None for line in lines):
        return None
    return quantize(sum((line.amount for line in lines if line.amount is not None), Decimal("0")))


def _invested(purchase: CostLine, first_mile: CostLine) -> Decimal | None:
    if not purchase.complete or not first_mile.complete or purchase.amount is None or first_mile.amount is None:
        return None
    return quantize(purchase.amount + first_mile.amount)


def _cite(source: str, verified_at: date | None) -> str:
    if verified_at is None:
        return source
    return f"{source}（核对于 {verified_at.isoformat()}）"


def _purchase(data: LandedCostInput, currency: str) -> CostLine:
    label = "采购成本"
    if data.purchase_amount is None or not data.purchase_currency:
        return _gap("PURCHASE", currency, f"未填写{label}。")
    if data.purchase_currency == currency:
        amount = quantize(data.purchase_amount)
        return _ok(
            "PURCHASE",
            amount,
            currency,
            f"采购币种与售价币种相同，{label} = {money_text(amount)} {currency}。",
            "本次输入",
        )
    if data.fx_rate is None or data.fx_rate <= 0:
        return _gap("PURCHASE", currency, "采购币种与售价币种不同，但没有填写汇率。")
    amount = quantize(data.purchase_amount / data.fx_rate)
    origin = data.fx_source.strip() or "未标注"
    return _ok(
        "PURCHASE",
        amount,
        currency,
        (
            f"{label} = {money_text(data.purchase_amount)} {data.purchase_currency}"
            f" / {money_text(data.fx_rate)} = {money_text(amount)} {currency}。"
            f"汇率来源：{origin}。"
        ),
        origin,
    )


def _pick_tax(taxes: tuple[TaxFact, ...] | list[TaxFact], tax_type: str, hs_code: str) -> TaxFact | None:
    rows = [row for row in taxes if row.tax_type == tax_type]
    specific = [row for row in rows if row.hs_code_pattern != "*" and hs_code.startswith(row.hs_code_pattern)]
    if specific:
        return max(specific, key=lambda row: len(row.hs_code_pattern))
    wild = [row for row in rows if row.hs_code_pattern == "*"]
    if len(wild) == 1:
        return wild[0]
    return None


def _blocked_by_threshold(amount: Decimal, currency: str, fact: TaxFact) -> bool | None:
    if fact.threshold_amount is None:
        return False
    if fact.threshold_currency != currency:
        return None
    return amount < fact.threshold_amount


def _duty(data: LandedCostInput, taxes: tuple[TaxFact, ...] | list[TaxFact], currency: str) -> CostLine:
    fact = _pick_tax(taxes, "DUTY", data.hs_code)
    if fact is None:
        return _gap("DUTY", currency, "未配置进口关税。")
    if data.declared_value is None or data.declared_currency != currency:
        return _gap("DUTY", currency, "申报价值必须填写，且币种与售价一致。", _cite(fact.source, fact.verified_at))
    blocked = _blocked_by_threshold(data.declared_value, currency, fact)
    if blocked is None:
        return _gap("DUTY", currency, "关税门槛币种与申报价值币种不一致。", _cite(fact.source, fact.verified_at))
    source = _cite(fact.source, fact.verified_at)
    if blocked:
        return _ok(
            "DUTY",
            Decimal("0"),
            currency,
            (
                f"申报价值 {money_text(data.declared_value)} 低于门槛"
                f" {money_text(fact.threshold_amount or Decimal('0'))} {fact.threshold_currency}，进口关税 = 0。"
            ),
            source,
        )
    amount = quantize(data.declared_value * fact.rate)
    return _ok(
        "DUTY",
        amount,
        currency,
        (
            f"进口关税 = 申报价值 {money_text(data.declared_value)} × 税率 {money_text(fact.rate)}"
            f" = {money_text(amount)} {currency}。"
        ),
        source,
    )


def _import_tax(
    data: LandedCostInput,
    taxes: tuple[TaxFact, ...] | list[TaxFact],
    currency: str,
    duty: CostLine,
) -> CostLine:
    picked = [_pick_tax(taxes, tax_type, data.hs_code) for tax_type in IMPORT_TAX_TYPES]
    facts = [fact for fact in picked if fact is not None]
    if not facts:
        return _gap("IMPORT_TAX", currency, "未配置进口环节税（VAT/GST/SST/PPN/销售税）。")
    if data.declared_value is None or data.declared_currency != currency or not duty.complete or duty.amount is None:
        return _gap("IMPORT_TAX", currency, "进口环节税依赖申报价值和已算出的关税。")
    parts: list[str] = []
    sources: list[str] = []
    total = Decimal("0")
    for fact in facts:
        blocked = _blocked_by_threshold(data.declared_value, currency, fact)
        source = _cite(fact.source, fact.verified_at)
        if blocked is None:
            return _gap("IMPORT_TAX", currency, f"{fact.tax_type} 门槛币种与申报价值币种不一致。", source)
        if blocked:
            parts.append(
                f"{fact.tax_type}：申报价值低于门槛 {money_text(fact.threshold_amount or Decimal('0'))}，税额 = 0"
            )
            sources.append(source)
            continue
        base = (data.declared_value + duty.amount) * Decimal(fact.basis_numerator) / Decimal(fact.basis_denominator)
        amount = quantize(base * fact.rate)
        total += amount
        parts.append(
            f"{fact.tax_type} = (申报价值 {money_text(data.declared_value)} + 关税 {money_text(duty.amount)})"
            f" × {fact.basis_numerator}/{fact.basis_denominator} × 税率 {money_text(fact.rate)}"
            f" = {money_text(amount)}"
        )
        sources.append(source)
    return _ok("IMPORT_TAX", total, currency, "；".join(parts) + "。", "；".join(sources))


def _pick_fees(fees: tuple[FeeFact, ...] | list[FeeFact], fee_code: str, channel: str) -> list[FeeFact] | None:
    rows = [row for row in fees if row.fee_code == fee_code]
    chosen: list[FeeFact] = []
    for label in sorted({row.label for row in rows}):
        exact = [row for row in rows if row.label == label and row.channel == channel]
        wild = [row for row in rows if row.label == label and row.channel == "*"]
        pool = exact or wild
        if not pool:
            continue
        if len(pool) != 1:
            return None
        chosen.append(pool[0])
    return chosen


def _brokerage(data: LandedCostInput, fees: tuple[FeeFact, ...] | list[FeeFact], currency: str) -> CostLine:
    chosen = _pick_fees(fees, "BROKERAGE", data.channel)
    if chosen is None:
        return _gap("BROKERAGE", currency, "报关清关费用规则不唯一。")
    if not chosen:
        return _gap("BROKERAGE", currency, "未配置报关清关固定费。")
    if any(row.charge != "FIXED" for row in chosen):
        return _gap("BROKERAGE", currency, "报关清关费必须按固定金额配置。")
    parts: list[str] = []
    sources: list[str] = []
    total = Decimal("0")
    for row in chosen:
        if row.currency != currency:
            return _gap(
                "BROKERAGE", currency, f"{row.label} 的币种与售价币种不一致。", _cite(row.source, row.verified_at)
            )
        total += quantize(row.amount)
        parts.append(f"{row.label} = {money_text(row.amount)} {currency}")
        sources.append(_cite(row.source, row.verified_at))
    return _ok("BROKERAGE", total, currency, "报关清关固定费 = " + " + ".join(parts) + "。", "；".join(sources))


def _storage(data: LandedCostInput, fees: tuple[FeeFact, ...] | list[FeeFact], currency: str) -> CostLine:
    if data.storage_days is None:
        return _gap("STORAGE", currency, "未填写仓储天数。")
    if data.storage_days == 0:
        return _ok("STORAGE", Decimal("0"), currency, "仓储天数 = 0，仓储费 = 0。", "本次输入")
    chosen = _pick_fees(fees, "STORAGE", data.channel)
    if chosen is None or len(chosen) != 1:
        return _gap("STORAGE", currency, "仓储费规则缺失或不唯一。")
    row = chosen[0]
    source = _cite(row.source, row.verified_at)
    if row.charge != "PER_CBM_DAY" or row.currency != currency:
        return _gap("STORAGE", currency, "仓储费须按每立方米每天的售价币种配置。", source)
    if data.volume_cm3 is None:
        return _gap("STORAGE", currency, "未填写体积，无法计算仓储费。", source)
    cbm = data.volume_cm3 / _CM3_PER_CBM
    amount = quantize(cbm * Decimal(data.storage_days) * row.amount)
    return _ok(
        "STORAGE",
        amount,
        currency,
        (
            f"仓储费 = 体积 {money_text(data.volume_cm3)} cm³ / {_CM3_PER_CBM}"
            f" × {data.storage_days} 天 × {money_text(row.amount)} = {money_text(amount)} {currency}。"
        ),
        source,
    )


def _first_mile(
    data: LandedCostInput,
    fees: tuple[FeeFact, ...] | list[FeeFact],
    currency: str,
    purchase: CostLine,
) -> CostLine:
    if data.first_mile_method in {"WEIGHT", "VOLUME", "VALUE"}:
        return _allocate(data, currency, purchase)
    if data.first_mile_method == "CHARGEABLE":
        return _chargeable(data, fees, currency)
    return _gap("FIRST_MILE", currency, "头程分摊方式不在支持列表中。")


def _allocate(data: LandedCostInput, currency: str, purchase: CostLine) -> CostLine:
    if data.shipment_cost is None or data.shipment_currency != currency:
        return _gap("FIRST_MILE", currency, "头程总额必须填写，且币种与售价一致。")
    unit, total, unit_name = _allocation_metrics(data, currency, purchase)
    if unit is None or total is None:
        return _gap("FIRST_MILE", currency, f"按{unit_name}分摊时，单件和整票的{unit_name}都要填写。")
    if total <= 0:
        return _gap("FIRST_MILE", currency, f"整票{unit_name}必须大于 0。")
    amount = quantize(data.shipment_cost * unit / total)
    return _ok(
        "FIRST_MILE",
        amount,
        currency,
        (
            f"头程物流费 = 整票 {money_text(data.shipment_cost)} × 单件{unit_name} {money_text(unit)}"
            f" / 整票{unit_name} {money_text(total)} = {money_text(amount)} {currency}。"
        ),
        "本次输入的头程总额",
    )


def _allocation_metrics(
    data: LandedCostInput,
    currency: str,
    purchase: CostLine,
) -> tuple[Decimal | None, Decimal | None, str]:
    if data.first_mile_method == "WEIGHT":
        return data.weight_g, data.shipment_weight_g, "重量"
    if data.first_mile_method == "VOLUME":
        return data.volume_cm3, data.shipment_volume_cm3, "体积"
    unit_value = data.declared_value if data.declared_currency == currency else None
    if unit_value is None and purchase.complete:
        unit_value = purchase.amount
    return unit_value, data.shipment_value, "货值"


def _chargeable(data: LandedCostInput, fees: tuple[FeeFact, ...] | list[FeeFact], currency: str) -> CostLine:
    chosen = _pick_fees(fees, "FIRST_MILE", data.channel)
    if chosen is None or len(chosen) != 1:
        return _gap("FIRST_MILE", currency, "头程计费规则缺失或不唯一。")
    row = chosen[0]
    source = _cite(row.source, row.verified_at)
    if row.currency != currency:
        return _gap("FIRST_MILE", currency, "头程计费币种与售价币种不一致。", source)
    if row.charge == "FIXED":
        return _ok(
            "FIRST_MILE", row.amount, currency, f"头程物流费 = 固定 {money_text(row.amount)} {currency}。", source
        )
    if row.charge == "PER_CBM":
        if data.volume_cm3 is None:
            return _gap("FIRST_MILE", currency, "按体积计头程时必须填写体积。", source)
        amount = quantize(data.volume_cm3 / _CM3_PER_CBM * row.amount)
        return _ok(
            "FIRST_MILE",
            amount,
            currency,
            (
                f"头程物流费 = {money_text(data.volume_cm3)} cm³ / {_CM3_PER_CBM}"
                f" × {money_text(row.amount)} = {money_text(amount)} {currency}。"
            ),
            source,
        )
    if row.charge != "PER_KG":
        return _gap("FIRST_MILE", currency, "头程计费方式不在支持列表中。", source)
    actual = None if data.weight_g is None else data.weight_g / _GRAMS_PER_KG
    volumetric = None
    if row.volumetric_divisor and data.volume_cm3 is not None:
        volumetric = data.volume_cm3 / Decimal(row.volumetric_divisor)
    present = [item for item in (actual, volumetric) if item is not None]
    if not present:
        return _gap("FIRST_MILE", currency, "按重量计头程时必须填写重量或可换算的体积。", source)
    chargeable = max(present)
    amount = quantize(chargeable * row.amount)
    basis = "实重与体积重取大" if volumetric is not None and actual is not None else "按已填写的重量"
    return _ok(
        "FIRST_MILE",
        amount,
        currency,
        (
            f"头程物流费 = 计费重 {money_text(chargeable)} kg × {money_text(row.amount)}"
            f" = {money_text(amount)} {currency}。{basis}。"
            + (f"体积重除数 {row.volumetric_divisor}。" if row.volumetric_divisor else "未配置体积重除数，只按实重。")
        ),
        source,
    )


def _fx_reserve(
    fees: tuple[FeeFact, ...] | list[FeeFact],
    channel: str,
    currency: str,
    parts: tuple[CostLine, ...],
) -> CostLine:
    base = _total(parts)
    chosen = _pick_fees(fees, "FX_RESERVE", channel)
    if chosen is None or len(chosen) != 1:
        return _gap("FX_RESERVE", currency, "汇兑预备金费率缺失或不唯一。")
    row = chosen[0]
    source = _cite(row.source, row.verified_at)
    if row.charge != "RATE":
        return _gap("FX_RESERVE", currency, "汇兑预备金必须按比例配置。", source)
    if base is None:
        return _gap("FX_RESERVE", currency, "到手成本其他项不完整，无法计算汇兑预备金。", source)
    amount = quantize(base * row.amount)
    return _ok(
        "FX_RESERVE",
        amount,
        currency,
        (
            f"汇兑预备金 = (采购 + 头程 + 关税 + 进口环节税 + 报关费 + 仓储费)"
            f" {money_text(base)} × 费率 {money_text(row.amount)} = {money_text(amount)} {currency}。"
        ),
        source,
    )


def _price_lines(
    data: LandedCostInput,
    fees: tuple[FeeFact, ...] | list[FeeFact],
    currency: str,
) -> tuple[CostLine, CostLine, CostLine, CostLine, CostLine]:
    price = data.selling_price
    return (
        _rate_of_price("COMMISSION", "平台佣金", price, fees, data.channel, currency),
        _rate_of_price("PAYMENT", "支付手续费", price, fees, data.channel, currency),
        _fulfillment(price, fees, data.channel, currency),
        _rate_of_price("ADS", "广告费", price, fees, data.channel, currency),
        _return_loss(price, fees, data.channel, currency),
    )


def _rate_of_price(
    code: str,
    title: str,
    price: Decimal | None,
    fees: tuple[FeeFact, ...] | list[FeeFact],
    channel: str,
    currency: str,
) -> CostLine:
    chosen = _pick_fees(fees, code, channel)
    if chosen is None or len(chosen) != 1:
        return _gap(code, currency, f"{title}费率缺失或不唯一。")
    row = chosen[0]
    source = _cite(row.source, row.verified_at)
    if row.charge != "RATE":
        return _gap(code, currency, f"{title}必须按售价比例配置。", source)
    if price is None:
        return _gap(code, currency, f"未填写售价，无法计算{title}。", source)
    amount = quantize(price * row.amount)
    return _ok(
        code,
        amount,
        currency,
        f"{title} = 售价 {money_text(price)} × 费率 {money_text(row.amount)} = {money_text(amount)} {currency}。",
        source,
    )


def _fulfillment(
    price: Decimal | None,
    fees: tuple[FeeFact, ...] | list[FeeFact],
    channel: str,
    currency: str,
) -> CostLine:
    chosen = _pick_fees(fees, "FULFILLMENT", channel)
    if chosen is None or len(chosen) != 1:
        return _gap("FULFILLMENT", currency, "平台物流费规则缺失或不唯一。")
    row = chosen[0]
    source = _cite(row.source, row.verified_at)
    if row.charge == "FIXED":
        if row.currency != currency:
            return _gap("FULFILLMENT", currency, "平台物流费币种与售价币种不一致。", source)
        return _ok(
            "FULFILLMENT",
            row.amount,
            currency,
            f"平台物流费 = 固定 {money_text(row.amount)} {currency}。",
            source,
        )
    if row.charge == "RATE":
        if price is None:
            return _gap("FULFILLMENT", currency, "未填写售价，无法按比例计算平台物流费。", source)
        amount = quantize(price * row.amount)
        return _ok(
            "FULFILLMENT",
            amount,
            currency,
            f"平台物流费 = 售价 {money_text(price)} × 费率 {money_text(row.amount)} = {money_text(amount)} {currency}。",
            source,
        )
    return _gap("FULFILLMENT", currency, "平台物流费须按固定金额或售价比例配置。", source)


def _return_loss(
    price: Decimal | None,
    fees: tuple[FeeFact, ...] | list[FeeFact],
    channel: str,
    currency: str,
) -> CostLine:
    return_rate = _pick_fees(fees, "RETURN_RATE", channel)
    loss_rate = _pick_fees(fees, "RETURN_LOSS", channel)
    if return_rate is None or loss_rate is None or len(return_rate) != 1 or len(loss_rate) != 1:
        return _gap("RETURN", currency, "退货率和损失率必须各配置一条。")
    left, right = return_rate[0], loss_rate[0]
    source = "；".join((_cite(left.source, left.verified_at), _cite(right.source, right.verified_at)))
    if left.charge != "RATE" or right.charge != "RATE":
        return _gap("RETURN", currency, "退货率和损失率都必须按比例配置。", source)
    if price is None:
        return _gap("RETURN", currency, "未填写售价，无法计算退货损失摊销。", source)
    amount = quantize(price * left.amount * right.amount)
    return _ok(
        "RETURN",
        amount,
        currency,
        (
            f"退货损失摊销 = 售价 {money_text(price)} × 退货率 {money_text(left.amount)}"
            f" × 损失率 {money_text(right.amount)} = {money_text(amount)} {currency}。"
        ),
        source,
    )
