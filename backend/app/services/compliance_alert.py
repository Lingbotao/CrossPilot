"""合规提醒。页面按当天数据实时计算；日扫描只负责落库和发信去重。"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import tenant_context
from app.db.session import owner_session_scope, session_scope
from app.models.compliance import (
    ALERT_EXPIRED,
    NOTICE_CERT_EXPIRY,
    NOTICE_TAX_EFFECTIVE,
    ComplianceNotice,
    CountryTaxRule,
)
from app.models.tenant import Tenant
from app.repositories.compliance import (
    ComplianceCertificateRepository,
    ComplianceNoticeRepository,
    CountryTaxRuleRepository,
    list_compliance_tenant_ids,
)
from app.schemas.compliance import ComplianceAlertView
from app.services.compliance_windows import cert_horizon, cert_window, tax_horizon, tax_window
from app.services.outbox import deliver_notice

ALERT_LIMIT = 200


def tax_summary(row: CountryTaxRule) -> str:
    return f"{row.country} {row.tax_type} {row.hs_code_pattern} 将于 {row.effective_from.isoformat()} 生效"


def cert_summary(cert_type: str, cert_no: str, market: str, expires_on: date, level: str) -> str:
    if level == ALERT_EXPIRED:
        return f"{market} {cert_type} {cert_no} 已于 {expires_on.isoformat()} 过期"
    return f"{market} {cert_type} {cert_no} 将于 {expires_on.isoformat()} 到期"


class ComplianceAlertService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.rules = CountryTaxRuleRepository(session)
        self.certs = ComplianceCertificateRepository(session)
        self.notices = ComplianceNoticeRepository(session)

    async def list_alerts(self, today: date | None = None) -> list[ComplianceAlertView]:
        today = today or datetime.now(UTC).date()
        notices = {
            (row.kind, row.level, row.ref_id, row.due_on): row
            for row in await self.notices.list_recent(limit=ALERT_LIMIT)
        }
        alerts: list[ComplianceAlertView] = []
        for kind, level, ref_id, due_on, summary in await self._windows(today):
            notice = notices.get((kind, level, ref_id, due_on))
            alerts.append(
                ComplianceAlertView(
                    kind=kind,
                    level=level,
                    ref_id=ref_id,
                    due_on=due_on,
                    summary=notice.summary if notice is not None else summary,
                    emailed_at=notice.emailed_at if notice is not None else None,
                )
            )
        return alerts

    async def dispatch(self, tenant_id: int, today: date) -> int:
        recipient = await self._recipient(tenant_id)
        sent = 0
        for kind, level, ref_id, due_on, summary in await self._windows(today):
            if level == ALERT_EXPIRED:
                continue
            if await self.notices.get_window(kind, level, ref_id, due_on) is not None:
                continue
            emailed_at = None
            if recipient:
                deliver_notice(kind=kind.lower(), recipient=recipient, summary=summary)
                emailed_at = datetime.now(UTC)
                sent += 1
            now = datetime.now(UTC)
            await self.notices.add(
                ComplianceNotice(
                    tenant_id=tenant_id,
                    kind=kind,
                    level=level,
                    ref_id=ref_id,
                    due_on=due_on,
                    summary=summary,
                    emailed_at=emailed_at,
                    created_at=now,
                    updated_at=now,
                )
            )
        return sent

    async def _windows(self, today: date) -> list[tuple[str, str, int, date, str]]:
        found: list[tuple[str, str, int, date, str]] = []
        for row in await self.rules.list_becoming_effective(today, tax_horizon(today)):
            level = tax_window(row.effective_from, today)
            if level is None:
                continue
            found.append((NOTICE_TAX_EFFECTIVE, level, row.id, row.effective_from, tax_summary(row)))
        for certificate, _sku_code in await self.certs.list_in_horizon(cert_horizon(today), limit=ALERT_LIMIT):
            level = cert_window(certificate.expires_at, today)
            if level is None:
                continue
            found.append(
                (
                    NOTICE_CERT_EXPIRY,
                    level,
                    certificate.id,
                    certificate.expires_at,
                    cert_summary(
                        certificate.cert_type,
                        certificate.cert_no,
                        certificate.market,
                        certificate.expires_at,
                        level,
                    ),
                )
            )
        return found

    async def _recipient(self, tenant_id: int) -> str:
        tenant = await self.session.get(Tenant, tenant_id)
        if tenant is None or not tenant.contact_email:
            return ""
        return tenant.contact_email


async def scan_compliance_alerts(today: date | None = None) -> dict[str, int]:
    """系统任务：先用 owner 列出有合规数据的租户，再逐个回到租户上下文。"""

    today = today or datetime.now(UTC).date()
    async with owner_session_scope() as session:
        tenant_ids = await list_compliance_tenant_ids(session)
    emailed = 0
    for tenant_id in tenant_ids:
        with tenant_context(tenant_id):
            async with session_scope(tenant_id) as session:
                emailed += await ComplianceAlertService(session).dispatch(tenant_id, today)
    return {"tenants": len(tenant_ids), "emailed": emailed}
