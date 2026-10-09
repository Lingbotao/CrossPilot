"""刊登前合规闸门与体检报告。

L1 阻止调用平台。L2 写进批次行的说明，刊登继续。
类目代码为空时不套认证要求，避免把没配置的商品全部判红。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.engines.compliance import (
    CODE_CERT_EXPIRED,
    CODE_HS_MISSING,
    LEVEL_L1,
    LEVEL_L2,
    CertFact,
    PublishFinding,
    RuleFact,
    SkuFact,
    SpuFact,
    catalog_findings,
    publish_findings,
)
from app.models.compliance import REQUIREMENT_ACTIVE
from app.models.locale import CONTENT_MARKETS
from app.models.platform import Shop
from app.models.product import Sku
from app.repositories.compliance import (
    CertRequirementRuleRepository,
    ComplianceCertificateRepository,
    ComplianceReportRepository,
)
from app.repositories.hs_code import SpuHsBindingRepository
from app.repositories.locale import SensitiveTermRepository
from app.repositories.product import SpuRepository
from app.schemas.compliance import ComplianceReportItem, ComplianceReportView
from app.services.locale_text import scan_sensitive

SCAN_LIMIT = 500
RULE_LIMIT = 500
ITEM_LIMIT = 200
_MARKETS = frozenset(CONTENT_MARKETS)


def finding_text(item: PublishFinding) -> str:
    place = item.market or "各市场"
    if item.code == CODE_HS_MISSING:
        if item.market:
            return f"{item.market} 未绑定 HS 编码。请在商品详情核对官方编码后绑定，再刊登。"
        return "还没有绑定任何市场的 HS 编码。请在商品详情核对官方编码后绑定。"
    if item.code == "CERT_MISSING":
        return f"{place} 缺少 {item.cert_type}。请在认证台账登记仍在有效期内的证书后再刊登。"
    if item.code == CODE_CERT_EXPIRED:
        return f"{place} 的 {item.cert_type} 已过期。请用新证书号登记后续期后再刊登。"
    if item.code == "CERT_EXPIRING":
        return f"{place} 的 {item.cert_type} 将在 {item.days_left} 天后到期。本次不拦截，请尽快续期。"
    return "合规校验未通过"


def sensitive_text(keyword: str) -> str:
    return f"标题含敏感词「{keyword}」。本次不拦截，请到多语言页确认。"


def blocking_code(findings: list[PublishFinding]) -> ErrorCode:
    codes = {item.code for item in findings if item.level == LEVEL_L1}
    if codes == {CODE_HS_MISSING}:
        return ErrorCode.HS_CODE_MISSING
    if codes == {CODE_CERT_EXPIRED}:
        return ErrorCode.CERTIFICATE_EXPIRED
    return ErrorCode.COMPLIANCE_CHECK_FAILED


class ComplianceCheckService:
    def __init__(self, session: AsyncSession) -> None:
        self.spus = SpuRepository(session)
        self.bindings = SpuHsBindingRepository(session)
        self.certs = ComplianceCertificateRepository(session)
        self.rules = CertRequirementRuleRepository(session)
        self.terms = SensitiveTermRepository(session)
        self.report_rows = ComplianceReportRepository(session)

    async def note_for_publish(self, *, sku: Sku, shop: Shop, title: str, lang: str) -> str | None:
        market = shop.site_code.upper()
        if market not in _MARKETS:
            return None
        today = datetime.now(UTC).date()
        spu = await self.spus.get_or_404(sku.spu_id)
        binding = await self.bindings.get_for_market(spu.id, market)
        required: set[str] = set()
        if spu.category_code:
            rows = await self.rules.list_active(market, spu.category_code)
            required = {row.cert_type for row in rows if row.status == REQUIREMENT_ACTIVE}
        held_until = await self._held_until(sku.id, market)
        findings = publish_findings(
            market=market,
            hs_bound=binding is not None,
            required=required,
            held_until=held_until,
            today=today,
        )
        blocked = [item for item in findings if item.level == LEVEL_L1]
        if blocked:
            raise AppError(
                _join(finding_text(item) for item in blocked),
                code=blocking_code(blocked),
                data={"market": market, "level": LEVEL_L1},
            )
        warnings = [finding_text(item) for item in findings if item.level == LEVEL_L2]
        warnings.extend(await self._sensitive_warnings(market, lang, title))
        if not warnings:
            return None
        return _join(warnings)

    async def report(self, today: date | None = None) -> ComplianceReportView:
        current = today or datetime.now(UTC).date()
        spus = await self.report_rows.spus(SCAN_LIMIT)
        spu_ids = [row.id for row in spus]
        skus = await self.report_rows.skus_for(spu_ids)
        bindings = await self.report_rows.hs_for(spu_ids)
        sku_ids = [row.id for row in skus]
        certs = await self.report_rows.certs_for(sku_ids)
        rules = await self.report_rows.active_rules(RULE_LIMIT)
        hs_markets: dict[int, set[str]] = {}
        for binding in bindings:
            hs_markets.setdefault(binding.spu_id, set()).add(binding.market)
        sku_code = {row.id: row.sku_code for row in skus}
        title_of = {row.id: row.title for row in spus}
        findings = catalog_findings(
            spus=[
                SpuFact(
                    spu_id=row.id,
                    title=row.title,
                    category_code=row.category_code,
                    hs_markets=frozenset(hs_markets.get(row.id, set())),
                )
                for row in spus
            ],
            skus=[SkuFact(sku_id=row.id, spu_id=row.spu_id, sku_code=row.sku_code) for row in skus],
            certs=[
                CertFact(
                    sku_id=row.sku_id,
                    market=row.market,
                    cert_type=row.cert_type,
                    expires_on=row.expires_at,
                )
                for row in certs
            ],
            rules=[
                RuleFact(market=row.market, category_code=row.category_code, cert_type=row.cert_type) for row in rules
            ],
            today=current,
        )
        ordered = sorted(findings, key=lambda item: (0 if item.level == LEVEL_L1 else 1, item.market or "", item.code))
        dirty = {item.spu_id for item in ordered if item.spu_id is not None}
        items = [
            ComplianceReportItem(
                level=item.level,
                code=item.code,
                spu_id=item.spu_id or 0,
                sku_id=item.sku_id,
                sku_code=None if item.sku_id is None else sku_code.get(item.sku_id),
                title=title_of.get(item.spu_id or 0, ""),
                market=item.market,
                cert_type=item.cert_type,
                summary=finding_text(item),
                fix_path=_fix_path(item),
            )
            for item in ordered[:ITEM_LIMIT]
        ]
        red = sum(1 for item in ordered if item.level == LEVEL_L1)
        yellow = sum(1 for item in ordered if item.level == LEVEL_L2)
        green = sum(1 for row in spus if row.id not in dirty)
        return ComplianceReportView(red=red, yellow=yellow, green=green, items=items)

    async def _held_until(self, sku_id: int, market: str) -> dict[str, date]:
        held: dict[str, date] = {}
        for row in await self.certs.list_for_sku_market(sku_id, market):
            current = held.get(row.cert_type)
            if current is None or row.expires_at > current:
                held[row.cert_type] = row.expires_at
        return held

    async def _sensitive_warnings(self, market: str, lang: str, title: str) -> list[str]:
        terms = await self.terms.list_for(market, lang)
        hits = scan_sensitive([("title", title)], [(item.keyword, item.suggest_replacement) for item in terms])
        return [sensitive_text(keyword) for _field, keyword, _suggestion in hits]


def _join(parts: Iterable[str]) -> str:
    text = "；".join(str(item) for item in parts)
    return text[:500]


def _fix_path(item: PublishFinding) -> str:
    if item.code == CODE_HS_MISSING and item.spu_id is not None:
        return f"/products/{item.spu_id}"
    return "/compliance/certificates"
