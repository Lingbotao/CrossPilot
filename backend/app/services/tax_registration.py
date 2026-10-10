"""税务注册台账。停用是软删除，不覆盖仍有效的同一税号。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models.compliance import TaxRegistration
from app.models.enums import AuditAction
from app.repositories.compliance import TaxRegistrationRepository
from app.repositories.identity import AuditLogRepository
from app.schemas.compliance import TaxRegistrationCreate, TaxRegistrationView

LIST_LIMIT = 200


def registration_view(row: TaxRegistration) -> TaxRegistrationView:
    return TaxRegistrationView(
        id=row.id,
        country=row.country,
        tax_type=row.tax_type,
        tax_no=row.tax_no,
        entity=row.entity,
        agent=row.agent,
        filing_cycle=row.filing_cycle,
    )


def _clean_key(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if len(text) > 128:
        return text[:128]
    return text or None


class TaxRegistrationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.rows = TaxRegistrationRepository(session)
        self.audit = AuditLogRepository(session)

    async def list_rows(self, *, country: str | None, limit: int) -> list[TaxRegistrationView]:
        found = await self.rows.list_visible(country=country, limit=min(limit, LIST_LIMIT))
        return [registration_view(row) for row in found]

    async def create(
        self,
        payload: TaxRegistrationCreate,
        *,
        tenant_id: int,
        actor_id: int,
        idempotency_key: str | None,
    ) -> TaxRegistrationView:
        key = _clean_key(idempotency_key)
        if key:
            existing = await self.rows.get_by_key(key)
            if existing is not None:
                return registration_view(existing)
        current = await self.rows.get_active(payload.country, payload.tax_type, payload.tax_no)
        if current is not None:
            raise ConflictError("同一国家、税种、税号已登记")
        row = TaxRegistration(
            tenant_id=tenant_id,
            country=payload.country,
            tax_type=payload.tax_type,
            tax_no=payload.tax_no,
            entity=payload.entity,
            agent=payload.agent,
            filing_cycle=payload.filing_cycle,
            idempotency_key=key,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.rows.add(row)
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.TAX_REGISTRATION,
            resource="tax_registration",
            resource_id=row.id,
            before=None,
            after={
                "country": row.country,
                "tax_type": row.tax_type,
                "tax_no": row.tax_no,
                "filing_cycle": row.filing_cycle,
            },
        )
        return registration_view(row)

    async def retire(self, registration_id: int, *, tenant_id: int, actor_id: int) -> TaxRegistrationView:
        row = await self.rows.get_or_404(registration_id)
        view = registration_view(row)
        row.updated_by = actor_id
        await self.rows.soft_delete(row)
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.TAX_REGISTRATION,
            resource="tax_registration",
            resource_id=registration_id,
            before={"tax_no": view.tax_no, "country": view.country},
            after={"deleted": "true"},
        )
        return view
