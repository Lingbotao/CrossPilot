"""税率版本。编辑是新版本，停用保留旧行。不按国家写税率分支。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.compliance import TAX_RULE_ACTIVE, TAX_RULE_DISABLED, CountryTaxRule
from app.models.enums import AuditAction
from app.repositories.compliance import CountryTaxRuleRepository
from app.repositories.identity import AuditLogRepository
from app.schemas.compliance import TaxRuleCreate, TaxRuleView, threshold_text
from app.services.compliance_windows import format_fraction, fraction_to_percent, percent_to_fraction, ranges_overlap

LIST_LIMIT = 200


def _snapshot(row: CountryTaxRule) -> dict[str, str]:
    return {
        "country": row.country,
        "tax_type": row.tax_type,
        "hs_code_pattern": row.hs_code_pattern,
        "version": str(row.version),
        "rate": format_fraction(row.rate),
        "source": row.source,
        "status": row.status,
    }


def tax_view(row: CountryTaxRule) -> TaxRuleView:
    currency = str(row.threshold_currency).strip() if row.threshold_currency else None
    return TaxRuleView(
        id=row.id,
        country=row.country,
        tax_type=row.tax_type,
        hs_code_pattern=row.hs_code_pattern,
        rate=format_fraction(row.rate),
        rate_percent=fraction_to_percent(row.rate),
        basis_numerator=row.basis_numerator,
        basis_denominator=row.basis_denominator,
        threshold_amount=threshold_text(row.threshold_amount),
        threshold_currency=currency or None,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        version=row.version,
        status=row.status,
        source=row.source,
        verified_by=str(row.verified_by),
        verified_at=row.verified_at,
    )


def retire_end(effective_from: date, effective_to: date | None, today: date) -> date:
    """给停用行写上结束日，并保证结束日晚于生效日。"""

    if effective_to is not None and effective_to <= today and effective_to > effective_from:
        return effective_to
    if today > effective_from:
        return today
    return effective_from + timedelta(days=1)


class TaxRuleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.rules = CountryTaxRuleRepository(session)
        self.audit = AuditLogRepository(session)

    async def list_rules(
        self,
        *,
        country: str | None,
        tax_type: str | None,
        limit: int,
    ) -> list[TaxRuleView]:
        rows = await self.rules.list_visible(country=country, tax_type=tax_type, limit=min(limit, LIST_LIMIT))
        return [tax_view(row) for row in rows]

    async def create(self, payload: TaxRuleCreate, *, tenant_id: int, actor_id: int) -> TaxRuleView:
        source = payload.source.strip()
        if not source:
            raise AppError("来源必须填写", code=ErrorCode.PARAM_INVALID)
        existing = await self.rules.list_for_key(payload.country, payload.tax_type, payload.hs_code_pattern)
        active = [row for row in existing if row.status == TAX_RULE_ACTIVE]
        ends = {row.id: row.effective_to for row in active}
        truncate: CountryTaxRule | None = None
        opens = [row for row in active if row.effective_to is None]
        if len(opens) == 1 and opens[0].effective_from < payload.effective_from:
            current = opens[0]
            if ranges_overlap(current.effective_from, None, payload.effective_from, payload.effective_to):
                ends[current.id] = payload.effective_from
                truncate = current
        for row in active:
            if ranges_overlap(row.effective_from, ends[row.id], payload.effective_from, payload.effective_to):
                raise AppError("与已有税率版本的生效区间重叠", code=ErrorCode.TAX_RULE_OVERLAP)
        before: dict[str, str] | None = None
        if truncate is not None:
            before = _snapshot(truncate)
            truncate.effective_to = payload.effective_from
            truncate.updated_by = actor_id
            self.rules.assert_tenant_owned(truncate)
        now = datetime.now(UTC)
        version = max((row.version for row in existing), default=0) + 1
        row = CountryTaxRule(
            tenant_id=tenant_id,
            country=payload.country,
            tax_type=payload.tax_type,
            hs_code_pattern=payload.hs_code_pattern,
            rate=percent_to_fraction(payload.rate),
            basis_numerator=payload.basis_numerator,
            basis_denominator=payload.basis_denominator,
            threshold_amount=payload.threshold_amount,
            threshold_currency=payload.threshold_currency,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            version=version,
            status=TAX_RULE_ACTIVE,
            source=source,
            verified_by=actor_id,
            verified_at=now,
            created_at=now,
            updated_at=now,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.rules.add(row)
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.TAX_RULE,
            resource="country_tax_rule",
            resource_id=row.id,
            before=before,
            after=_snapshot(row),
        )
        return tax_view(row)

    async def retire(self, rule_id: int, *, tenant_id: int, actor_id: int, today: date | None = None) -> TaxRuleView:
        row = await self.rules.get_or_404(rule_id)
        if row.status == TAX_RULE_DISABLED:
            return tax_view(row)
        today = today or datetime.now(UTC).date()
        before = _snapshot(row)
        row.status = TAX_RULE_DISABLED
        row.effective_to = retire_end(row.effective_from, row.effective_to, today)
        row.updated_by = actor_id
        self.rules.assert_tenant_owned(row)
        await self.session.flush()
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.TAX_RULE,
            resource="country_tax_rule",
            resource_id=row.id,
            before=before,
            after=_snapshot(row),
        )
        return tax_view(row)
