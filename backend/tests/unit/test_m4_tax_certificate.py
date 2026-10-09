"""税率版本与认证台账：新版本不覆盖旧行，提醒窗口按整天计算。"""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.core.errors import AppError, ErrorCode, NotFoundError, ParamInvalidError
from app.db.base import SoftDeleteMixin, TenantMixin
from app.models import TENANT_SCOPED_TABLES
from app.models.compliance import (
    TAX_RULE_ACTIVE,
    CertRequirementRule,
    ComplianceCertificate,
    ComplianceNotice,
    CountryTaxRule,
)
from app.models.enums import AuditAction
from app.models.product import Sku
from app.schemas.compliance import CertificateCreate, TaxRuleCreate
from app.services.certificate import CertificateService, parse_certificate_table
from app.services.compliance_alert import ComplianceAlertService
from app.services.compliance_windows import cert_gaps, cert_window, percent_to_fraction, tax_window
from app.services.outbox import reset_outbox
from app.services.tax_rule import TaxRuleService

_MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0018_tax_certificate.py"
_SERVICES = Path(__file__).resolve().parents[2] / "app" / "services"
_TODAY = date(2026, 10, 9)


def test_compliance_tables_are_tenant_ledgers() -> None:
    for model, table in (
        (CountryTaxRule, "country_tax_rule"),
        (ComplianceCertificate, "compliance_certificate"),
        (CertRequirementRule, "cert_requirement_rule"),
        (ComplianceNotice, "compliance_notice"),
    ):
        assert issubclass(model, TenantMixin)
        assert not issubclass(model, SoftDeleteMixin)
        assert table in TENANT_SCOPED_TABLES
    assert AuditAction.TAX_RULE in AuditAction.ALL
    assert AuditAction.CERT_RECORD in AuditAction.ALL
    text = _MIGRATION.read_text(encoding="utf-8")
    assert text.count('_tenant_policy("') == 4
    assert text.count('_grant_ledger("') == 4
    assert "INSERT INTO country_tax_rule" not in text
    assert "INSERT INTO cert_requirement_rule" not in text


def test_services_do_not_branch_on_a_statutory_fraction() -> None:
    for name in ("tax_rule.py", "certificate.py", "compliance_alert.py", "compliance_windows.py"):
        text = (_SERVICES / name).read_text(encoding="utf-8")
        assert "11/12" not in text
        assert "11 / 12" not in text


def test_percent_becomes_a_fraction_and_windows_use_whole_days() -> None:
    assert percent_to_fraction(Decimal("9")) == Decimal("0.090000")
    assert percent_to_fraction(Decimal("9.5")) == Decimal("0.095000")
    with pytest.raises(ParamInvalidError):
        percent_to_fraction(Decimal("-1"))
    assert tax_window(_TODAY, _TODAY) == "D7"
    assert tax_window(date(2026, 10, 16), _TODAY) == "D7"
    assert tax_window(date(2026, 10, 17), _TODAY) is None
    assert cert_window(_TODAY, _TODAY) == "D7"
    assert cert_window(date(2026, 10, 16), _TODAY) == "D7"
    assert cert_window(date(2026, 10, 17), _TODAY) == "D30"
    assert cert_window(date(2026, 11, 8), _TODAY) == "D30"
    assert cert_window(date(2026, 11, 9), _TODAY) == "D60"
    assert cert_window(date(2026, 12, 8), _TODAY) == "D60"
    assert cert_window(date(2026, 12, 9), _TODAY) is None
    assert cert_window(date(2026, 10, 8), _TODAY) == "EXPIRED"
    gaps = cert_gaps({"FCC", "FDA"}, {"FDA": date(2026, 10, 8)}, _TODAY)
    assert gaps == [("FCC", "MISSING"), ("FDA", "EXPIRED")]


def test_blank_source_and_unpaired_threshold_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TaxRuleCreate.model_validate(_tax_payload(source="  "))
    with pytest.raises(ValidationError):
        TaxRuleCreate.model_validate(_tax_payload(threshold_amount="10", threshold_currency=None))
    with pytest.raises(ValidationError):
        TaxRuleCreate.model_validate(_tax_payload(country="ZZ"))
    payload = TaxRuleCreate.model_validate(_tax_payload(basis_numerator=11, basis_denominator=12))
    assert payload.basis_numerator == 11
    assert payload.basis_denominator == 12
    assert payload.rate == Decimal("9")


def _tax_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "country": "SG",
        "tax_type": "GST",
        "hs_code_pattern": "*",
        "rate": "9",
        "effective_from": "2026-11-01",
        "source": "IRAS GST guide",
    }
    payload.update(overrides)
    return payload


def _stamp(row: CountryTaxRule | ComplianceCertificate | CertRequirementRule) -> None:
    now = datetime.now(UTC)
    row.id = row.id or 50
    row.created_at = now
    row.updated_at = row.updated_at or now


def _rule(**overrides: object) -> CountryTaxRule:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "id": 1,
        "tenant_id": 9,
        "country": "SG",
        "tax_type": "GST",
        "hs_code_pattern": "*",
        "rate": Decimal("0.070000"),
        "basis_numerator": 1,
        "basis_denominator": 1,
        "effective_from": date(2020, 1, 1),
        "effective_to": None,
        "version": 1,
        "status": TAX_RULE_ACTIVE,
        "source": "旧出处",
        "verified_by": 3,
        "verified_at": now,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return CountryTaxRule(**values)  # type: ignore[arg-type]


def _tax_service() -> TaxRuleService:
    session = MagicMock()
    session.flush = AsyncMock()
    service = TaxRuleService(session)
    service.rules = MagicMock()
    service.audit = MagicMock()
    service.audit.append_action = AsyncMock()
    service.rules.assert_tenant_owned = MagicMock()

    async def add(row: CountryTaxRule) -> CountryTaxRule:
        _stamp(row)
        return row

    service.rules.add = add
    return service


async def test_new_tax_version_closes_the_open_row_and_keeps_basis_from_the_payload() -> None:
    service = _tax_service()
    current = _rule()
    service.rules.list_for_key = AsyncMock(return_value=[current])
    payload = TaxRuleCreate.model_validate(_tax_payload(basis_numerator=11, basis_denominator=12))
    view = await service.create(payload, tenant_id=9, actor_id=7)
    assert current.effective_to == date(2026, 11, 1)
    assert current.status == TAX_RULE_ACTIVE
    assert view.version == 2
    assert view.rate == "0.090000"
    assert view.rate_percent == "9.000000"
    assert view.basis_numerator == 11
    assert view.basis_denominator == 12
    kwargs = service.audit.append_action.await_args.kwargs
    assert kwargs["action"] == AuditAction.TAX_RULE
    assert kwargs["before"]["version"] == "1"
    assert kwargs["after"]["version"] == "2"
    assert kwargs["after"]["rate"] == "0.090000"
    assert kwargs["after"]["source"] == "IRAS GST guide"


async def test_overlapping_tax_range_is_rejected_and_retire_keeps_the_row() -> None:
    service = _tax_service()
    current = _rule(effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 1))
    service.rules.list_for_key = AsyncMock(return_value=[current])
    payload = TaxRuleCreate.model_validate(_tax_payload(effective_from="2026-03-01", effective_to="2026-04-01"))
    with pytest.raises(AppError) as caught:
        await service.create(payload, tenant_id=9, actor_id=7)
    assert caught.value.code == ErrorCode.TAX_RULE_OVERLAP
    assert current.effective_to == date(2026, 6, 1)

    open_row = _rule(id=8, effective_from=_TODAY, effective_to=None)
    service.rules.get_or_404 = AsyncMock(return_value=open_row)
    view = await service.retire(8, tenant_id=9, actor_id=7, today=_TODAY)
    assert open_row.status == "DISABLED"
    assert open_row.effective_to == date(2026, 10, 10)
    assert view.status == "DISABLED"
    assert service.rules.get_or_404.await_count == 1


def _sku() -> Sku:
    now = datetime.now(UTC)
    return Sku(
        id=21,
        tenant_id=9,
        spu_id=11,
        sku_code="TEE-1",
        weight_g=Decimal("1"),
        length_cm=Decimal("1"),
        width_cm=Decimal("1"),
        height_cm=Decimal("1"),
        created_at=now,
        updated_at=now,
    )


def _certificate(**overrides: object) -> ComplianceCertificate:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "id": 4,
        "tenant_id": 9,
        "sku_id": 21,
        "market": "US",
        "cert_type": "FCC",
        "cert_no": "FCC-1",
        "issued_at": date(2024, 1, 1),
        "expires_at": date(2025, 1, 1),
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return ComplianceCertificate(**values)  # type: ignore[arg-type]


def _cert_service() -> CertificateService:
    session = MagicMock()
    session.flush = AsyncMock()
    service = CertificateService(session, store=MagicMock())
    service.certs = MagicMock()
    service.requirements = MagicMock()
    service.skus = MagicMock()
    service.spus = MagicMock()
    service.audit = MagicMock()
    service.audit.append_action = AsyncMock()
    service.certs.assert_tenant_owned = MagicMock()
    service.requirements.assert_tenant_owned = MagicMock()

    async def add(row: ComplianceCertificate) -> ComplianceCertificate:
        _stamp(row)
        return row

    service.certs.add = add
    return service


def test_import_with_a_bad_row_parses_no_writes() -> None:
    table = [
        (1, ["SKU编码", "市场", "认证类型", "证书号", "签发日", "到期日"]),
        (2, ["TEE-1", "US", "FCC", "FCC-1", "2024-01-01", "2027-01-01"]),
        (3, ["TEE-1", "ZZ", "FCC", "FCC-2", "2024-01-01", "2027-01-01"]),
    ]
    parsed, errors = parse_certificate_table(table)
    assert len(parsed) == 1
    assert errors[0]["row"] == 3


async def test_import_rejects_the_batch_and_repeat_key_updates_expiry() -> None:
    service = _cert_service()
    added: list[object] = []

    async def add(row: object) -> object:
        added.append(row)
        return row

    service.certs.add = add
    payload = (
        "SKU编码,市场,认证类型,证书号,签发日,到期日\n"
        "TEE-1,US,FCC,FCC-1,2024-01-01,2024-06-01\n"
        "TEE-1,ZZ,FCC,FCC-2,2024-01-01,2024-06-01\n"
    ).encode()
    with pytest.raises(AppError) as caught:
        await service.import_file(payload, "certs.csv", tenant_id=9, actor_id=7)
    assert caught.value.code == ErrorCode.PARAM_INVALID
    assert added == []

    existing = _certificate()
    service.skus.get_by_code = AsyncMock(return_value=_sku())
    service.certs.get_natural = AsyncMock(return_value=existing)
    good = b"sku_code,market,cert_type,cert_no,issued_at,expires_at\nTEE-1,US,FCC,FCC-1,2024-01-01,2028-01-01\n"
    result = await service.import_file(good, "certs.csv", tenant_id=9, actor_id=7)
    assert result.imported == 0
    assert result.updated == 1
    assert existing.expires_at == date(2028, 1, 1)
    kwargs = service.audit.append_action.await_args.kwargs
    assert kwargs["action"] == AuditAction.CERT_RECORD
    assert kwargs["after"]["updated"] == 1


async def test_unknown_sku_is_not_found_and_expired_certificate_is_a_gap() -> None:
    service = _cert_service()
    service.skus.get_or_404 = AsyncMock(side_effect=NotFoundError())
    payload = CertificateCreate.model_validate(
        {
            "sku_id": "21",
            "market": "US",
            "cert_type": "FCC",
            "cert_no": "FCC-9",
            "issued_at": "2024-01-01",
            "expires_at": "2028-01-01",
        }
    )
    with pytest.raises(NotFoundError):
        await service.create(payload, tenant_id=9, actor_id=7)

    service.skus.get_or_404 = AsyncMock(return_value=_sku())
    service.requirements.list_active = AsyncMock(
        return_value=[
            CertRequirementRule(
                id=3,
                tenant_id=9,
                market="US",
                category_code="wireless",
                cert_type="FCC",
                source="operator note",
                status="ACTIVE",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        ]
    )
    service.certs.list_for_sku_market = AsyncMock(return_value=[_certificate(expires_at=date(2026, 10, 8))])
    gaps = await service.gaps(sku_id=21, market="US", category_code="wireless", today=_TODAY)
    assert [(item.cert_type, item.reason) for item in gaps] == [("FCC", "EXPIRED")]


async def test_alert_dispatch_emails_once_and_skips_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_outbox()
    session = MagicMock()
    service = ComplianceAlertService(session)
    service.rules = MagicMock()
    service.certs = MagicMock()
    service.notices = MagicMock()
    rule = _rule(id=15, effective_from=date(2026, 10, 12))
    expired = _certificate(id=16, expires_at=date(2026, 10, 8))
    service.rules.list_becoming_effective = AsyncMock(return_value=[rule])
    service.certs.list_in_horizon = AsyncMock(return_value=[(expired, "TEE-1")])
    stored: dict[tuple[str, str, int, date], object] = {}

    async def get_window(kind: str, level: str, ref_id: int, due_on: date) -> object | None:
        return stored.get((kind, level, ref_id, due_on))

    async def add(row: ComplianceNotice) -> ComplianceNotice:
        stored[(row.kind, row.level, row.ref_id, row.due_on)] = row
        return row

    service.notices.get_window = get_window
    service.notices.add = add
    tenant = MagicMock()
    tenant.contact_email = "ops@example.com"
    session.get = AsyncMock(return_value=tenant)
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        "app.services.compliance_alert.deliver_notice",
        lambda **kwargs: calls.append(kwargs),
    )
    assert await service.dispatch(9, _TODAY) == 1
    assert await service.dispatch(9, _TODAY) == 0
    assert len(calls) == 1
    assert calls[0]["recipient"] == "ops@example.com"
    assert list(stored) == [("TAX_EFFECTIVE", "D7", 15, date(2026, 10, 12))]
