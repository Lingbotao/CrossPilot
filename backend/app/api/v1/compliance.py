"""税率规则、认证台账与合规提醒。跨租户资源返回 404。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile

from app.core.deps import DbSession, Identity, require_permission
from app.core.errors import ParamInvalidError
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.compliance import (
    CertGapView,
    CertificateCreate,
    CertificateImportResult,
    CertificateView,
    CertRequirementCreate,
    CertRequirementView,
    ComplianceAlertView,
    ComplianceReportView,
    TaxRuleCreate,
    TaxRuleView,
    clean_tax_type,
)
from app.schemas.listing import parse_id
from app.schemas.locale import clean_market
from app.services.certificate import CertificateService
from app.services.compliance_alert import ComplianceAlertService
from app.services.compliance_check import ComplianceCheckService
from app.services.tax_rule import TaxRuleService

router = APIRouter(tags=["合规"])

ComplianceReader = Annotated[Identity, Depends(require_permission(Perm.COMPLIANCE_READ))]
ComplianceWriter = Annotated[Identity, Depends(require_permission(Perm.COMPLIANCE_WRITE))]


def _optional_id(raw: str | None, label: str) -> int | None:
    if raw is None or not raw.strip():
        return None
    try:
        return parse_id(raw)
    except ValueError as exc:
        raise ParamInvalidError(f"{label}不合法") from exc


def _optional_market(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    try:
        return clean_market(raw)
    except ValueError as exc:
        raise ParamInvalidError("市场不在支持列表中") from exc


def _optional_tax_type(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    try:
        return clean_tax_type(raw)
    except ValueError as exc:
        raise ParamInvalidError("税种不在支持列表中") from exc


@router.get("/tax-rules", response_model=ApiResponse[list[TaxRuleView]], summary="税率版本列表")
async def list_tax_rules(
    identity: ComplianceReader,
    session: DbSession,
    country: Annotated[str | None, Query()] = None,
    tax_type: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ApiResponse[list[TaxRuleView]]:
    del identity
    data = await TaxRuleService(session).list_rules(
        country=_optional_market(country),
        tax_type=_optional_tax_type(tax_type),
        limit=limit,
    )
    return ok(data)


@router.post("/tax-rules", response_model=ApiResponse[TaxRuleView], summary="新增税率版本")
async def create_tax_rule(
    payload: TaxRuleCreate,
    identity: ComplianceWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[TaxRuleView]:
    data = await TaxRuleService(session).create(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="税率版本已保存")


@router.post("/tax-rules/{rule_id}/retire", response_model=ApiResponse[TaxRuleView], summary="停用税率版本")
async def retire_tax_rule(
    rule_id: int,
    identity: ComplianceWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[TaxRuleView]:
    data = await TaxRuleService(session).retire(
        rule_id,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="税率版本已停用")


@router.get("/certificates", response_model=ApiResponse[list[CertificateView]], summary="认证台账")
async def list_certificates(
    identity: ComplianceReader,
    session: DbSession,
    sku_id: Annotated[str | None, Query()] = None,
    spu_id: Annotated[str | None, Query()] = None,
    market: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ApiResponse[list[CertificateView]]:
    del identity
    data = await CertificateService(session).list_certificates(
        sku_id=_optional_id(sku_id, "SKU"),
        spu_id=_optional_id(spu_id, "商品"),
        market=_optional_market(market),
        limit=limit,
    )
    return ok(data)


@router.post("/certificates", response_model=ApiResponse[CertificateView], summary="登记认证")
async def create_certificate(
    payload: CertificateCreate,
    identity: ComplianceWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CertificateView]:
    data = await CertificateService(session).create(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="认证已登记")


@router.post(
    "/certificates/import",
    response_model=ApiResponse[CertificateImportResult],
    summary="批量导入认证",
)
async def import_certificates(
    identity: ComplianceWriter,
    session: DbSession,
    file: Annotated[UploadFile, File()],
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CertificateImportResult]:
    body = await file.read()
    data = await CertificateService(session).import_file(
        body,
        file.filename or "upload.csv",
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="认证已导入")


@router.post(
    "/certificates/{certificate_id}/file",
    response_model=ApiResponse[CertificateView],
    summary="上传认证附件",
)
async def upload_certificate_file(
    certificate_id: int,
    identity: ComplianceWriter,
    session: DbSession,
    file: Annotated[UploadFile, File()],
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CertificateView]:
    body = await file.read()
    data = await CertificateService(session).attach_file(
        certificate_id,
        body,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="附件已上传")


@router.get("/certificates/gaps", response_model=ApiResponse[list[CertGapView]], summary="查询缺失认证")
async def certificate_gaps(
    identity: ComplianceReader,
    session: DbSession,
    sku_id: Annotated[str, Query()],
    market: Annotated[str, Query()],
    category_code: Annotated[str, Query(min_length=1, max_length=64)],
) -> ApiResponse[list[CertGapView]]:
    del identity
    try:
        parsed_market = clean_market(market)
    except ValueError as exc:
        raise ParamInvalidError("市场不在支持列表中") from exc
    parsed_sku = _optional_id(sku_id, "SKU")
    if parsed_sku is None:
        raise ParamInvalidError("SKU 不合法")
    data = await CertificateService(session).gaps(
        sku_id=parsed_sku,
        market=parsed_market,
        category_code=category_code.strip(),
    )
    return ok(data)


@router.get("/cert-requirements", response_model=ApiResponse[list[CertRequirementView]], summary="认证要求")
async def list_cert_requirements(
    identity: ComplianceReader,
    session: DbSession,
    market: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ApiResponse[list[CertRequirementView]]:
    del identity
    data = await CertificateService(session).list_requirements(market=_optional_market(market), limit=limit)
    return ok(data)


@router.post("/cert-requirements", response_model=ApiResponse[CertRequirementView], summary="新增认证要求")
async def create_cert_requirement(
    payload: CertRequirementCreate,
    identity: ComplianceWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CertRequirementView]:
    data = await CertificateService(session).create_requirement(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="认证要求已保存")


@router.post(
    "/cert-requirements/{rule_id}/retire",
    response_model=ApiResponse[CertRequirementView],
    summary="停用认证要求",
)
async def retire_cert_requirement(
    rule_id: int,
    identity: ComplianceWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[CertRequirementView]:
    data = await CertificateService(session).retire_requirement(
        rule_id,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="认证要求已停用")


@router.get("/compliance-report", response_model=ApiResponse[ComplianceReportView], summary="合规体检")
async def compliance_report(
    identity: ComplianceReader,
    session: DbSession,
) -> ApiResponse[ComplianceReportView]:
    del identity
    return ok(await ComplianceCheckService(session).report())


@router.get("/compliance-alerts", response_model=ApiResponse[list[ComplianceAlertView]], summary="合规提醒")
async def list_compliance_alerts(
    identity: ComplianceReader,
    session: DbSession,
) -> ApiResponse[list[ComplianceAlertView]]:
    del identity
    return ok(await ComplianceAlertService(session).list_alerts())
