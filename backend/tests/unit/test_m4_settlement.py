"""税务注册与结算匹配：未匹配单列，跨币种不折算。"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.core.errors import ErrorCode, SettlementFileInvalidError
from app.db.base import SoftDeleteMixin, TenantMixin
from app.engines.settlement import NOTE_CURRENCY, NOTE_UNMATCHED, OrderRef, SettlementLine, header_amount, reconcile
from app.models import TENANT_SCOPED_TABLES
from app.models.compliance import TaxRegistration
from app.models.enums import AuditAction
from app.models.finance import Settlement, SettlementItem
from app.services.settlement import parse_settlement_table

_MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0022_tax_registration_settlement.py"


def test_registration_is_master_data_and_settlement_is_a_ledger() -> None:
    assert issubclass(TaxRegistration, TenantMixin)
    assert issubclass(TaxRegistration, SoftDeleteMixin)
    assert issubclass(Settlement, TenantMixin)
    assert issubclass(SettlementItem, TenantMixin)
    assert not issubclass(Settlement, SoftDeleteMixin)
    assert not issubclass(SettlementItem, SoftDeleteMixin)
    assert "tax_registration" in TENANT_SCOPED_TABLES
    assert "settlement" in TENANT_SCOPED_TABLES
    assert "settlement_item" in TENANT_SCOPED_TABLES
    assert AuditAction.TAX_REGISTRATION in AuditAction.ALL
    assert AuditAction.SETTLEMENT_IMPORT in AuditAction.ALL
    text = _MIGRATION.read_text(encoding="utf-8")
    assert text.count('_tenant_policy("') == 3
    assert text.count('_grant_ledger("') == 3
    assert "INSERT INTO tax_registration" not in text
    assert "INSERT INTO settlement " not in text
    assert ErrorCode.SETTLEMENT_FILE_INVALID == 70007


def test_matched_and_unmatched_lines_keep_a_decimal_rate() -> None:
    lines = [
        SettlementLine("A-1", "COMMISSION", Decimal("1.20"), "USD"),
        SettlementLine("A-1", "PAYMENT", Decimal("0.30"), "USD"),
        SettlementLine("MISSING", "COMMISSION", Decimal("2"), "USD"),
    ]
    orders = {"A-1": OrderRef(order_id=9, total_amount=Decimal("10"), currency="USD")}
    matched, gaps, rate = reconcile(lines, orders)
    assert [line.status for line in matched] == ["MATCHED", "MATCHED", "UNMATCHED"]
    assert matched[2].order_id is None
    assert rate == Decimal("0.666667")
    by_order = {gap.platform_order_id: gap for gap in gaps}
    assert by_order["A-1"].deviation == Decimal("-8.500000")
    assert by_order["MISSING"].deviation is None
    assert by_order["MISSING"].note == NOTE_UNMATCHED
    assert header_amount(lines, "USD") == Decimal("3.500000")


def test_currency_mismatch_leaves_the_deviation_empty() -> None:
    lines = [SettlementLine("A-1", "COMMISSION", Decimal("7.2"), "CNY")]
    orders = {"A-1": OrderRef(order_id=3, total_amount=Decimal("1"), currency="USD")}
    _matched, gaps, rate = reconcile(lines, orders)
    assert rate == Decimal("1.000000")
    assert gaps[0].deviation is None
    assert gaps[0].note == NOTE_CURRENCY
    assert gaps[0].order_amount == Decimal("1.000000")


def test_bad_settlement_file_is_rejected() -> None:
    with pytest.raises(SettlementFileInvalidError) as caught:
        parse_settlement_table([(1, ["order", "fee", "amount", "currency"])])
    assert caught.value.code == ErrorCode.SETTLEMENT_FILE_INVALID
    with pytest.raises(SettlementFileInvalidError):
        parse_settlement_table(
            [
                (1, ["platform_order_id", "fee_type", "amount", "currency"]),
                (2, ["A-1", "COMMISSION", "nope", "USD"]),
            ]
        )
    parsed = parse_settlement_table(
        [
            (1, ["platform_order_id", "fee_type", "amount", "currency"]),
            (2, ["A-1", "COMMISSION", "1.500000", "usd"]),
        ]
    )
    assert parsed[0].currency == "USD"
    assert parsed[0].amount == Decimal("1.500000")
