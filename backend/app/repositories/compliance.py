"""税率、认证、要求规则与提醒。SQL 只留在这一层。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import (
    REQUIREMENT_ACTIVE,
    TAX_RULE_ACTIVE,
    CertRequirementRule,
    ComplianceCertificate,
    ComplianceNotice,
    CountryTaxRule,
    TaxRegistration,
)
from app.models.hs_code import SpuHsBinding
from app.models.product import Sku, Spu
from app.repositories.base import BaseRepository


class CountryTaxRuleRepository(BaseRepository[CountryTaxRule]):
    model = CountryTaxRule

    async def list_for_key(self, country: str, tax_type: str, pattern: str) -> list[CountryTaxRule]:
        stmt = (
            self.base_select()
            .where(
                CountryTaxRule.country == country,
                CountryTaxRule.tax_type == tax_type,
                CountryTaxRule.hs_code_pattern == pattern,
            )
            .order_by(CountryTaxRule.version.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_visible(
        self,
        *,
        country: str | None,
        tax_type: str | None,
        limit: int,
    ) -> list[CountryTaxRule]:
        stmt = self.base_select()
        if country is not None:
            stmt = stmt.where(CountryTaxRule.country == country)
        if tax_type is not None:
            stmt = stmt.where(CountryTaxRule.tax_type == tax_type)
        stmt = stmt.order_by(
            CountryTaxRule.country.asc(),
            CountryTaxRule.tax_type.asc(),
            CountryTaxRule.hs_code_pattern.asc(),
            CountryTaxRule.version.desc(),
        ).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_effective(self, country: str, on: date) -> list[CountryTaxRule]:
        stmt = self.base_select().where(
            CountryTaxRule.country == country,
            CountryTaxRule.status == TAX_RULE_ACTIVE,
            CountryTaxRule.effective_from <= on,
            or_(CountryTaxRule.effective_to.is_(None), CountryTaxRule.effective_to > on),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_becoming_effective(self, today: date, until: date) -> list[CountryTaxRule]:
        stmt = self.base_select().where(
            CountryTaxRule.status == TAX_RULE_ACTIVE,
            CountryTaxRule.effective_from >= today,
            CountryTaxRule.effective_from <= until,
        )
        return list((await self.session.execute(stmt)).scalars().all())


class ComplianceCertificateRepository(BaseRepository[ComplianceCertificate]):
    model = ComplianceCertificate

    async def get_natural(
        self,
        sku_id: int,
        market: str,
        cert_type: str,
        cert_no: str,
    ) -> ComplianceCertificate | None:
        return await self.get_by(sku_id=sku_id, market=market, cert_type=cert_type, cert_no=cert_no)

    async def list_filtered(
        self,
        *,
        sku_id: int | None,
        spu_id: int | None,
        market: str | None,
        limit: int,
    ) -> list[tuple[ComplianceCertificate, str]]:
        stmt = (
            select(ComplianceCertificate, Sku.sku_code)
            .join(Sku, Sku.id == ComplianceCertificate.sku_id)
            .where(Sku.deleted_at.is_(None))
        )
        if sku_id is not None:
            stmt = stmt.where(ComplianceCertificate.sku_id == sku_id)
        if spu_id is not None:
            stmt = stmt.where(Sku.spu_id == spu_id)
        if market is not None:
            stmt = stmt.where(ComplianceCertificate.market == market)
        stmt = stmt.order_by(ComplianceCertificate.expires_at.asc(), ComplianceCertificate.id.asc()).limit(limit)
        return [(row, code) for row, code in (await self.session.execute(stmt)).all()]

    async def list_for_sku_market(self, sku_id: int, market: str) -> list[ComplianceCertificate]:
        stmt = self.base_select().where(
            ComplianceCertificate.sku_id == sku_id,
            ComplianceCertificate.market == market,
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_in_horizon(self, horizon: date, *, limit: int) -> list[tuple[ComplianceCertificate, str]]:
        stmt = (
            select(ComplianceCertificate, Sku.sku_code)
            .join(Sku, Sku.id == ComplianceCertificate.sku_id)
            .where(Sku.deleted_at.is_(None), ComplianceCertificate.expires_at <= horizon)
            .order_by(ComplianceCertificate.expires_at.asc())
            .limit(limit)
        )
        return [(row, code) for row, code in (await self.session.execute(stmt)).all()]


class CertRequirementRuleRepository(BaseRepository[CertRequirementRule]):
    model = CertRequirementRule

    async def get_natural(self, market: str, category_code: str, cert_type: str) -> CertRequirementRule | None:
        return await self.get_by(market=market, category_code=category_code, cert_type=cert_type)

    async def list_active(self, market: str, category_code: str) -> list[CertRequirementRule]:
        stmt = self.base_select().where(
            CertRequirementRule.market == market,
            CertRequirementRule.category_code == category_code,
            CertRequirementRule.status == REQUIREMENT_ACTIVE,
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_visible(self, *, market: str | None, limit: int) -> list[CertRequirementRule]:
        stmt = self.base_select()
        if market is not None:
            stmt = stmt.where(CertRequirementRule.market == market)
        stmt = stmt.order_by(
            CertRequirementRule.market.asc(),
            CertRequirementRule.category_code.asc(),
            CertRequirementRule.cert_type.asc(),
        ).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())


class ComplianceNoticeRepository(BaseRepository[ComplianceNotice]):
    model = ComplianceNotice

    async def get_window(self, kind: str, level: str, ref_id: int, due_on: date) -> ComplianceNotice | None:
        return await self.get_by(kind=kind, level=level, ref_id=ref_id, due_on=due_on)

    async def list_recent(self, *, limit: int) -> list[ComplianceNotice]:
        stmt = self.base_select().order_by(ComplianceNotice.due_on.asc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())


async def list_compliance_tenant_ids(session: AsyncSession) -> list[int]:
    """系统扫描用。调用方必须是 owner 会话，这里不套租户条件。"""

    rows = await session.execute(
        text(
            """
            SELECT tenant_id FROM country_tax_rule
            UNION
            SELECT tenant_id FROM compliance_certificate
            """
        )
    )
    return [int(item) for item in rows.scalars().all()]


class ComplianceReportRepository:
    """体检用的只读查询。租户条件由 ORM 过滤器加上。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def spus(self, limit: int) -> list[Spu]:
        stmt = select(Spu).where(Spu.deleted_at.is_(None)).order_by(Spu.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def skus_for(self, spu_ids: list[int]) -> list[Sku]:
        if not spu_ids:
            return []
        stmt = select(Sku).where(Sku.deleted_at.is_(None), Sku.spu_id.in_(spu_ids)).order_by(Sku.id.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def hs_for(self, spu_ids: list[int]) -> list[SpuHsBinding]:
        if not spu_ids:
            return []
        stmt = select(SpuHsBinding).where(SpuHsBinding.spu_id.in_(spu_ids))
        return list((await self.session.execute(stmt)).scalars().all())

    async def certs_for(self, sku_ids: list[int]) -> list[ComplianceCertificate]:
        if not sku_ids:
            return []
        stmt = select(ComplianceCertificate).where(ComplianceCertificate.sku_id.in_(sku_ids))
        return list((await self.session.execute(stmt)).scalars().all())

    async def active_rules(self, limit: int) -> list[CertRequirementRule]:
        stmt = (
            select(CertRequirementRule)
            .where(CertRequirementRule.status == REQUIREMENT_ACTIVE)
            .order_by(CertRequirementRule.id.asc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())


class TaxRegistrationRepository(BaseRepository[TaxRegistration]):
    model = TaxRegistration

    async def get_by_key(self, key: str) -> TaxRegistration | None:
        return await self.get_by(idempotency_key=key)

    async def get_active(self, country: str, tax_type: str, tax_no: str) -> TaxRegistration | None:
        return await self.get_by(country=country, tax_type=tax_type, tax_no=tax_no)

    async def list_visible(self, *, country: str | None, limit: int) -> list[TaxRegistration]:
        stmt = self.base_select()
        if country is not None:
            stmt = stmt.where(TaxRegistration.country == country)
        stmt = stmt.order_by(TaxRegistration.country.asc(), TaxRegistration.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())
