"""刊登前 L1/L2 与体检分类。不写法定税率，也不把没配置的类目判成缺失。"""

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import AppError, ErrorCode
from app.engines.compliance import (
    LEVEL_L1,
    LEVEL_L2,
    CertFact,
    RuleFact,
    SkuFact,
    SpuFact,
    catalog_findings,
    publish_findings,
)
from app.services.compliance_check import ComplianceCheckService, blocking_code

TODAY = date(2026, 10, 9)


def test_missing_hs_is_l1_and_blank_category_does_not_invent_a_cert() -> None:
    findings = publish_findings(market="SG", hs_bound=False, required=set(), held_until={}, today=TODAY)
    assert [item.code for item in findings] == ["HS_MISSING"]
    assert findings[0].level == LEVEL_L1


def test_required_gap_blocks_and_a_live_certificate_inside_sixty_days_only_warns() -> None:
    missing = publish_findings(
        market="CA",
        hs_bound=True,
        required={"FDA"},
        held_until={},
        today=TODAY,
    )
    assert [(item.level, item.code) for item in missing] == [(LEVEL_L1, "CERT_MISSING")]
    expiring = publish_findings(
        market="CA",
        hs_bound=True,
        required={"FDA"},
        held_until={"FDA": date(2026, 10, 20)},
        today=TODAY,
    )
    assert [(item.level, item.code, item.days_left) for item in expiring] == [(LEVEL_L2, "CERT_EXPIRING", 11)]
    expired = publish_findings(
        market="CA",
        hs_bound=True,
        required={"FDA"},
        held_until={"FDA": date(2026, 10, 1)},
        today=TODAY,
    )
    assert expired[0].code == "CERT_EXPIRED"
    assert blocking_code(expired) == ErrorCode.CERTIFICATE_EXPIRED
    assert blocking_code(missing) == ErrorCode.COMPLIANCE_CHECK_FAILED


def test_catalog_counts_unbound_hs_expired_certs_and_ignores_products_without_a_category() -> None:
    findings = catalog_findings(
        spus=[
            SpuFact(spu_id=1, title="杯子", category_code=None, hs_markets=frozenset()),
            SpuFact(spu_id=2, title="玩具", category_code="TOY", hs_markets=frozenset({"CA"})),
        ],
        skus=[SkuFact(sku_id=8, spu_id=2, sku_code="MUG")],
        certs=[CertFact(sku_id=8, market="CA", cert_type="FDA", expires_on=date(2026, 10, 1))],
        rules=[RuleFact(market="CA", category_code="TOY", cert_type="FDA")],
        today=TODAY,
    )
    codes = [(item.spu_id, item.code) for item in findings]
    assert codes.count((1, "HS_MISSING")) == 1
    assert codes.count((2, "CERT_EXPIRED")) == 1
    assert all(item.spu_id != 1 or item.code == "HS_MISSING" for item in findings)


def test_a_newer_certificate_keeps_an_older_expired_copy_off_the_report() -> None:
    findings = catalog_findings(
        spus=[SpuFact(spu_id=2, title="玩具", category_code=None, hs_markets=frozenset({"CA"}))],
        skus=[SkuFact(sku_id=8, spu_id=2, sku_code="MUG")],
        certs=[
            CertFact(sku_id=8, market="CA", cert_type="FDA", expires_on=date(2026, 1, 1)),
            CertFact(sku_id=8, market="CA", cert_type="FDA", expires_on=date(2027, 1, 1)),
        ],
        rules=[],
        today=TODAY,
    )
    assert findings == []


def test_engine_does_not_embed_a_statutory_rate() -> None:
    source = Path(__file__).resolve().parents[2].joinpath("app/engines/compliance.py").read_text(encoding="utf-8")
    assert "11/12" not in source
    assert "0.09" not in source


@pytest.mark.asyncio
async def test_publish_note_blocks_on_missing_hs_and_warns_on_a_sensitive_word() -> None:
    service = ComplianceCheckService(MagicMock())
    service.spus.get_or_404 = AsyncMock(return_value=SimpleNamespace(id=1, category_code=None))
    service.bindings.get_for_market = AsyncMock(return_value=None)
    service.certs.list_for_sku_market = AsyncMock(return_value=[])
    sku = SimpleNamespace(id=8, spu_id=1)
    shop = SimpleNamespace(site_code="sg")
    with pytest.raises(AppError) as blocked:
        await service.note_for_publish(sku=sku, shop=shop, title="magic cure", lang="en")
    assert blocked.value.code == ErrorCode.HS_CODE_MISSING

    service.bindings.get_for_market = AsyncMock(return_value=SimpleNamespace(id=3))
    service.terms.list_for = AsyncMock(return_value=[SimpleNamespace(keyword="cure", suggest_replacement=None)])
    note = await service.note_for_publish(sku=sku, shop=shop, title="magic cure", lang="en")
    assert note is not None
    assert "cure" in note
    assert "不拦截" in note
