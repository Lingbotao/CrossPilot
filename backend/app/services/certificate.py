"""认证台账、附件与批量导入。有错误行时整批不落库。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.images import max_upload_bytes
from app.core.errors import AppError, ErrorCode, ParamInvalidError
from app.db.snowflake import next_snowflake_id
from app.models.compliance import REQUIREMENT_ACTIVE, REQUIREMENT_DISABLED, CertRequirementRule, ComplianceCertificate
from app.models.enums import AuditAction
from app.models.product import Sku
from app.object_store import ObjectStore, default_object_store
from app.repositories.compliance import CertRequirementRuleRepository, ComplianceCertificateRepository
from app.repositories.identity import AuditLogRepository
from app.repositories.product import SkuRepository, SpuRepository
from app.schemas.compliance import (
    CertGapView,
    CertificateCreate,
    CertificateImportResult,
    CertificateView,
    CertRequirementCreate,
    CertRequirementView,
    clean_cert_type,
)
from app.schemas.locale import clean_market
from app.services.compliance_windows import cert_gaps
from app.services.spreadsheet import SpreadsheetError, read_tabular

LIST_LIMIT = 200
MAX_IMPORT_ROWS = 500
MAX_SHEET_BYTES = 2_000_000
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("sku_code", "SKU编码"),
    ("market", "市场"),
    ("cert_type", "认证类型"),
    ("cert_no", "证书号"),
    ("issued_at", "签发日"),
    ("expires_at", "到期日"),
)
_HEADER_KEYS = {key: key for key, _label in _COLUMNS}
_HEADER_KEYS.update({label: key for key, label in _COLUMNS})
_EXTENSIONS = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
}
RowError = dict[str, object]


@dataclass(frozen=True, slots=True)
class CertificateRow:
    row: int
    sku_code: str
    market: str
    cert_type: str
    cert_no: str
    issued_at: date
    expires_at: date


def sniff_certificate(body: bytes) -> str:
    if body.startswith(b"%PDF-"):
        return "application/pdf"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise AppError("认证附件只接受 PDF、PNG 或 JPEG", code=ErrorCode.CERT_FILE_INVALID)


def parse_certificate_table(table: list[tuple[int, list[str]]]) -> tuple[list[CertificateRow], list[RowError]]:
    if not table:
        return [], [{"row": 1, "message": "文件是空的"}]
    header_number, header_cells = table[0]
    columns = [_HEADER_KEYS.get(cell.strip()) for cell in header_cells]
    if any(key not in columns for key, _label in _COLUMNS):
        return [], [{"row": header_number, "message": "表头需要 SKU编码、市场、认证类型、证书号、签发日、到期日"}]
    if len(table) - 1 > MAX_IMPORT_ROWS:
        return [], [{"row": header_number, "message": f"一次最多导入 {MAX_IMPORT_ROWS} 行"}]
    index = {key: columns.index(key) for key, _label in _COLUMNS}
    parsed: list[CertificateRow] = []
    errors: list[RowError] = []
    seen: set[tuple[str, str, str, str]] = set()
    for number, cells in table[1:]:
        item, row_errors = _parse_row(number, cells, index, seen)
        errors.extend(row_errors)
        if item is not None:
            parsed.append(item)
    return parsed, errors


def _cell(cells: list[str], index: dict[str, int], key: str) -> str:
    position = index[key]
    if position >= len(cells):
        return ""
    return cells[position].strip()


def _parse_row(
    number: int,
    cells: list[str],
    index: dict[str, int],
    seen: set[tuple[str, str, str, str]],
) -> tuple[CertificateRow | None, list[RowError]]:
    errors: list[RowError] = []
    sku_code = _cell(cells, index, "sku_code")
    cert_no = _cell(cells, index, "cert_no")
    if not sku_code:
        errors.append({"row": number, "message": "SKU编码必须填写"})
    if not cert_no:
        errors.append({"row": number, "message": "证书号必须填写"})
    try:
        market = clean_market(_cell(cells, index, "market"))
    except ValueError as exc:
        errors.append({"row": number, "message": str(exc)})
        market = ""
    try:
        cert_type = clean_cert_type(_cell(cells, index, "cert_type"))
    except ValueError as exc:
        errors.append({"row": number, "message": str(exc)})
        cert_type = ""
    try:
        issued_at = date.fromisoformat(_cell(cells, index, "issued_at"))
        expires_at = date.fromisoformat(_cell(cells, index, "expires_at"))
    except ValueError:
        errors.append({"row": number, "message": "日期需要写成 YYYY-MM-DD"})
        issued_at = None
        expires_at = None
    if issued_at is not None and expires_at is not None and expires_at < issued_at:
        errors.append({"row": number, "message": "到期日不能早于签发日"})
    if errors or issued_at is None or expires_at is None:
        return None, errors
    key = (sku_code, market, cert_type, cert_no)
    if key in seen:
        return None, [{"row": number, "message": "文件内证书重复"}]
    seen.add(key)
    return (
        CertificateRow(
            row=number,
            sku_code=sku_code,
            market=market,
            cert_type=cert_type,
            cert_no=cert_no,
            issued_at=issued_at,
            expires_at=expires_at,
        ),
        [],
    )


def _reject_rows(rows: list[RowError]) -> None:
    raise AppError("导入文件有错误，没有写入任何认证", code=ErrorCode.PARAM_INVALID, data={"rows": rows})


def certificate_view(row: ComplianceCertificate, sku_code: str) -> CertificateView:
    return CertificateView(
        id=row.id,
        sku_id=row.sku_id,
        sku_code=sku_code,
        market=row.market,
        cert_type=row.cert_type,
        cert_no=row.cert_no,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        object_key=row.object_key,
        content_type=row.content_type,
        updated_at=row.updated_at,
    )


def requirement_view(row: CertRequirementRule) -> CertRequirementView:
    return CertRequirementView(
        id=row.id,
        market=row.market,
        category_code=row.category_code,
        cert_type=row.cert_type,
        source=row.source,
        status=row.status,
        updated_at=row.updated_at,
    )


class CertificateService:
    def __init__(self, session: AsyncSession, *, store: ObjectStore | None = None) -> None:
        self.session = session
        self.certs = ComplianceCertificateRepository(session)
        self.requirements = CertRequirementRuleRepository(session)
        self.skus = SkuRepository(session)
        self.spus = SpuRepository(session)
        self.audit = AuditLogRepository(session)
        self.store = store if store is not None else default_object_store()

    async def list_certificates(
        self,
        *,
        sku_id: int | None,
        spu_id: int | None,
        market: str | None,
        limit: int,
    ) -> list[CertificateView]:
        if spu_id is not None:
            await self.spus.get_or_404(spu_id)
        if sku_id is not None:
            await self.skus.get_or_404(sku_id)
        rows = await self.certs.list_filtered(
            sku_id=sku_id,
            spu_id=spu_id,
            market=market,
            limit=min(limit, LIST_LIMIT),
        )
        return [certificate_view(row, sku_code) for row, sku_code in rows]

    async def create(self, payload: CertificateCreate, *, tenant_id: int, actor_id: int) -> CertificateView:
        sku = await self.skus.get_or_404(payload.sku_id)
        return await self._save(
            sku,
            market=payload.market,
            cert_type=payload.cert_type,
            cert_no=payload.cert_no,
            issued_at=payload.issued_at,
            expires_at=payload.expires_at,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )

    async def import_file(
        self,
        payload: bytes,
        filename: str,
        *,
        tenant_id: int,
        actor_id: int,
    ) -> CertificateImportResult:
        if len(payload) > MAX_SHEET_BYTES:
            raise ParamInvalidError("导入文件过大")
        try:
            table = read_tabular(payload, filename or "upload.csv")
        except SpreadsheetError as exc:
            raise ParamInvalidError(str(exc)) from exc
        parsed, errors = parse_certificate_table(table)
        if errors:
            _reject_rows(errors)
        missing: list[RowError] = []
        ready: list[tuple[CertificateRow, Sku]] = []
        for item in parsed:
            sku = await self.skus.get_by_code(item.sku_code)
            if sku is None:
                missing.append({"row": item.row, "message": "SKU 不存在"})
            else:
                ready.append((item, sku))
        if missing:
            _reject_rows(missing)
        imported = 0
        updated = 0
        for item, sku in ready:
            existing = await self.certs.get_natural(sku.id, item.market, item.cert_type, item.cert_no)
            if existing is None:
                imported += 1
            else:
                updated += 1
            await self._save(
                sku,
                market=item.market,
                cert_type=item.cert_type,
                cert_no=item.cert_no,
                issued_at=item.issued_at,
                expires_at=item.expires_at,
                tenant_id=tenant_id,
                actor_id=actor_id,
                write_audit=False,
            )
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.CERT_RECORD,
            resource="compliance_certificate",
            after={"imported": imported, "updated": updated},
        )
        return CertificateImportResult(imported=imported, updated=updated)

    async def attach_file(
        self,
        certificate_id: int,
        body: bytes,
        *,
        tenant_id: int,
        actor_id: int,
    ) -> CertificateView:
        if len(body) > max_upload_bytes():
            raise AppError("认证附件超过允许大小", code=ErrorCode.CERT_FILE_INVALID)
        content_type = sniff_certificate(body)
        row = await self.certs.get_or_404(certificate_id)
        sku = await self.skus.get_or_404(row.sku_id)
        extension = _EXTENSIONS[content_type]
        key = f"{tenant_id}/certificates/{next_snowflake_id()}.{extension}"
        stored = self.store.put(key, body, content_type)
        before = {"object_key": row.object_key or ""}
        row.object_key = stored
        row.content_type = content_type
        row.updated_by = actor_id
        self.certs.assert_tenant_owned(row)
        await self.session.flush()
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.CERT_RECORD,
            resource="compliance_certificate",
            resource_id=row.id,
            before=before,
            after={"object_key": stored, "content_type": content_type},
        )
        return certificate_view(row, sku.sku_code)

    async def gaps(
        self,
        *,
        sku_id: int,
        market: str,
        category_code: str,
        today: date | None = None,
    ) -> list[CertGapView]:
        await self.skus.get_or_404(sku_id)
        today = today or datetime.now(UTC).date()
        required = {row.cert_type for row in await self.requirements.list_active(market, category_code.strip())}
        held: dict[str, date] = {}
        for row in await self.certs.list_for_sku_market(sku_id, market):
            current = held.get(row.cert_type)
            if current is None or row.expires_at > current:
                held[row.cert_type] = row.expires_at
        return [
            CertGapView(cert_type=cert_type, reason=reason) for cert_type, reason in cert_gaps(required, held, today)
        ]

    async def list_requirements(self, *, market: str | None, limit: int) -> list[CertRequirementView]:
        rows = await self.requirements.list_visible(market=market, limit=min(limit, LIST_LIMIT))
        return [requirement_view(row) for row in rows]

    async def create_requirement(
        self,
        payload: CertRequirementCreate,
        *,
        tenant_id: int,
        actor_id: int,
    ) -> CertRequirementView:
        source = payload.source.strip()
        existing = await self.requirements.get_natural(payload.market, payload.category_code, payload.cert_type)
        now = datetime.now(UTC)
        if existing is None:
            row = CertRequirementRule(
                tenant_id=tenant_id,
                market=payload.market,
                category_code=payload.category_code,
                cert_type=payload.cert_type,
                source=source,
                status=REQUIREMENT_ACTIVE,
                created_at=now,
                updated_at=now,
                created_by=actor_id,
                updated_by=actor_id,
            )
            await self.requirements.add(row)
            before = None
        elif existing.status == REQUIREMENT_ACTIVE and existing.source == source:
            return requirement_view(existing)
        else:
            before = {
                "market": existing.market,
                "category_code": existing.category_code,
                "cert_type": existing.cert_type,
                "source": existing.source,
                "status": existing.status,
            }
            existing.source = source
            existing.status = REQUIREMENT_ACTIVE
            existing.updated_by = actor_id
            self.requirements.assert_tenant_owned(existing)
            await self.session.flush()
            row = existing
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.CERT_RECORD,
            resource="cert_requirement_rule",
            resource_id=row.id,
            before=before,
            after={
                "market": row.market,
                "category_code": row.category_code,
                "cert_type": row.cert_type,
                "source": row.source,
                "status": row.status,
            },
        )
        return requirement_view(row)

    async def retire_requirement(self, rule_id: int, *, tenant_id: int, actor_id: int) -> CertRequirementView:
        row = await self.requirements.get_or_404(rule_id)
        if row.status == REQUIREMENT_DISABLED:
            return requirement_view(row)
        before = {"status": row.status, "cert_type": row.cert_type, "market": row.market}
        row.status = REQUIREMENT_DISABLED
        row.updated_by = actor_id
        self.requirements.assert_tenant_owned(row)
        await self.session.flush()
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.CERT_RECORD,
            resource="cert_requirement_rule",
            resource_id=row.id,
            before=before,
            after={"status": row.status, "cert_type": row.cert_type, "market": row.market},
        )
        return requirement_view(row)

    async def _save(
        self,
        sku: Sku,
        *,
        market: str,
        cert_type: str,
        cert_no: str,
        issued_at: date,
        expires_at: date,
        tenant_id: int,
        actor_id: int,
        write_audit: bool = True,
    ) -> CertificateView:
        existing = await self.certs.get_natural(sku.id, market, cert_type, cert_no)
        now = datetime.now(UTC)
        if existing is None:
            row = ComplianceCertificate(
                tenant_id=tenant_id,
                sku_id=sku.id,
                market=market,
                cert_type=cert_type,
                cert_no=cert_no,
                issued_at=issued_at,
                expires_at=expires_at,
                created_at=now,
                updated_at=now,
                created_by=actor_id,
                updated_by=actor_id,
            )
            await self.certs.add(row)
            before = None
        else:
            before = {
                "cert_no": existing.cert_no,
                "issued_at": existing.issued_at.isoformat(),
                "expires_at": existing.expires_at.isoformat(),
            }
            existing.issued_at = issued_at
            existing.expires_at = expires_at
            existing.updated_by = actor_id
            self.certs.assert_tenant_owned(existing)
            await self.session.flush()
            row = existing
        if write_audit:
            await self.audit.append_action(
                tenant_id=tenant_id,
                user_id=actor_id,
                action=AuditAction.CERT_RECORD,
                resource="compliance_certificate",
                resource_id=row.id,
                before=before,
                after={
                    "sku_id": str(sku.id),
                    "market": market,
                    "cert_type": cert_type,
                    "cert_no": cert_no,
                    "expires_at": expires_at.isoformat(),
                },
            )
        return certificate_view(row, sku.sku_code)


__all__ = [
    "CertificateRow",
    "CertificateService",
    "parse_certificate_table",
    "sniff_certificate",
]
