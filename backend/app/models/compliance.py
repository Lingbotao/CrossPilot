"""税率版本、认证台账、认证要求与提醒去重（M4-02 / M4-03）。

四张表都是租户数据：运营人员录入并核对，不能做成全局可写词典。
税率比例、计税基础和认证要求都来自行数据。本模块不写法定税率，也不按国家分支。
台账和版本历史不软删除。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, TenantMixin
from app.models.locale import CONTENT_MARKETS

TAX_TYPES: tuple[str, ...] = (
    "DUTY",
    "VAT",
    "GST",
    "SST",
    "PPN",
    "SALES_TAX",
    "MPF",
    "OTHER",
)
TAX_RULE_ACTIVE = "ACTIVE"
TAX_RULE_DISABLED = "DISABLED"
TAX_RULE_STATUSES: tuple[str, ...] = (TAX_RULE_ACTIVE, TAX_RULE_DISABLED)
CERT_TYPES: tuple[str, ...] = (
    "FCC",
    "FDA",
    "CPC",
    "UL_ETL",
    "TIS",
    "TH_FDA",
    "SNI",
    "BPOM",
    "CR",
    "SAFETY_MARK",
    "SIRIM",
    "OTHER",
)
REQUIREMENT_ACTIVE = "ACTIVE"
REQUIREMENT_DISABLED = "DISABLED"
NOTICE_TAX_EFFECTIVE = "TAX_EFFECTIVE"
NOTICE_CERT_EXPIRY = "CERT_EXPIRY"
NOTICE_KINDS: tuple[str, ...] = (NOTICE_TAX_EFFECTIVE, NOTICE_CERT_EXPIRY)
NOTICE_LEVELS: tuple[str, ...] = ("D7", "D30", "D60")
ALERT_EXPIRED = "EXPIRED"

_MARKET_SQL = ", ".join(f"'{item}'" for item in CONTENT_MARKETS)
_TAX_TYPE_SQL = ", ".join(f"'{item}'" for item in TAX_TYPES)
_TAX_STATUS_SQL = ", ".join(f"'{item}'" for item in TAX_RULE_STATUSES)
_CERT_TYPE_SQL = ", ".join(f"'{item}'" for item in CERT_TYPES)
_NOTICE_KIND_SQL = ", ".join(f"'{item}'" for item in NOTICE_KINDS)
_NOTICE_LEVEL_SQL = ", ".join(f"'{item}'" for item in NOTICE_LEVELS)


def _sql_in(name: str, sql: str) -> CheckConstraint:
    return CheckConstraint(f"{name} IN ({sql})", name=name)


class CountryTaxRule(Base, PKMixin, TenantMixin, AuditMixin):
    """同一国家、税种、HS 模式按版本保留。停用和截断都不删旧行。"""

    __tablename__ = "country_tax_rule"

    country: Mapped[str] = mapped_column(String(2), nullable=False)
    tax_type: Mapped[str] = mapped_column(String(16), nullable=False)
    hs_code_pattern: Mapped[str] = mapped_column(String(16), nullable=False, default="*", server_default="*")
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    basis_numerator: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    basis_denominator: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    threshold_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    threshold_currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TAX_RULE_ACTIVE, server_default=TAX_RULE_ACTIVE
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    verified_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "country",
            "tax_type",
            "hs_code_pattern",
            "version",
            name="uq_tax_rule_key_version",
        ),
        _sql_in("country", _MARKET_SQL),
        _sql_in("tax_type", _TAX_TYPE_SQL),
        _sql_in("status", _TAX_STATUS_SQL),
        CheckConstraint(
            "hs_code_pattern = '*' OR hs_code_pattern ~ '^[0-9]{2,10}$'",
            name="hs_code_pattern",
        ),
        CheckConstraint("rate >= 0 AND rate <= 10", name="rate"),
        CheckConstraint("basis_numerator >= 1 AND basis_denominator >= 1", name="basis"),
        CheckConstraint(
            "(threshold_amount IS NULL AND threshold_currency IS NULL) "
            "OR (threshold_amount IS NOT NULL AND threshold_currency IS NOT NULL AND threshold_amount >= 0)",
            name="threshold",
        ),
        CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="effective"),
        CheckConstraint("char_length(btrim(source)) > 0", name="source"),
        CheckConstraint("version >= 1", name="version"),
        Index("ix_country_tax_rule_tenant_id_country", "tenant_id", "country"),
    )


class ComplianceCertificate(Base, PKMixin, TenantMixin, AuditMixin):
    """SKU 在一个市场的一张证书。续期用新证书号另起一行。"""

    __tablename__ = "compliance_certificate"

    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    market: Mapped[str] = mapped_column(String(2), nullable=False)
    cert_type: Mapped[str] = mapped_column(String(16), nullable=False)
    cert_no: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[date] = mapped_column(Date, nullable=False)
    expires_at: Mapped[date] = mapped_column(Date, nullable=False)
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_compliance_certificate_sku_id_sku"),
        UniqueConstraint(
            "tenant_id",
            "sku_id",
            "market",
            "cert_type",
            "cert_no",
            name="uq_certificate_natural",
        ),
        _sql_in("market", _MARKET_SQL),
        _sql_in("cert_type", _CERT_TYPE_SQL),
        CheckConstraint("char_length(btrim(cert_no)) > 0", name="cert_no"),
        CheckConstraint("expires_at >= issued_at", name="expires"),
        CheckConstraint(
            "(object_key IS NULL AND content_type IS NULL) OR (object_key IS NOT NULL AND content_type IS NOT NULL)",
            name="file",
        ),
        Index("ix_compliance_certificate_tenant_id_sku_id", "tenant_id", "sku_id"),
        Index("ix_compliance_certificate_tenant_id_expires_at", "tenant_id", "expires_at"),
    )


class CertRequirementRule(Base, PKMixin, TenantMixin, AuditMixin):
    """市场 × 运营自填类目代码 → 必需认证。没有类目主数据，所以类目是字符串。"""

    __tablename__ = "cert_requirement_rule"

    market: Mapped[str] = mapped_column(String(2), nullable=False)
    category_code: Mapped[str] = mapped_column(String(64), nullable=False)
    cert_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=REQUIREMENT_ACTIVE,
        server_default=REQUIREMENT_ACTIVE,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "market",
            "category_code",
            "cert_type",
            name="uq_cert_requirement",
        ),
        _sql_in("market", _MARKET_SQL),
        _sql_in("cert_type", _CERT_TYPE_SQL),
        _sql_in("status", _TAX_STATUS_SQL),
        CheckConstraint("char_length(btrim(category_code)) > 0", name="category_code"),
        CheckConstraint("char_length(btrim(source)) > 0", name="source"),
        Index("ix_cert_requirement_rule_tenant_id_market", "tenant_id", "market"),
    )


class ComplianceNotice(Base, PKMixin, TenantMixin, AuditMixin):
    """同一提醒窗口只记一行，用来避免重复发信，也作为站内信。"""

    __tablename__ = "compliance_notice"

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    ref_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "kind",
            "level",
            "ref_id",
            "due_on",
            name="uq_compliance_notice",
        ),
        _sql_in("kind", _NOTICE_KIND_SQL),
        _sql_in("level", _NOTICE_LEVEL_SQL),
        CheckConstraint("char_length(btrim(summary)) > 0", name="summary"),
        Index("ix_compliance_notice_tenant_id_kind", "tenant_id", "kind"),
    )


__all__ = [
    "ALERT_EXPIRED",
    "CERT_TYPES",
    "NOTICE_CERT_EXPIRY",
    "NOTICE_KINDS",
    "NOTICE_LEVELS",
    "NOTICE_TAX_EFFECTIVE",
    "REQUIREMENT_ACTIVE",
    "REQUIREMENT_DISABLED",
    "TAX_RULE_ACTIVE",
    "TAX_RULE_DISABLED",
    "TAX_RULE_STATUSES",
    "TAX_TYPES",
    "CertRequirementRule",
    "ComplianceCertificate",
    "ComplianceNotice",
    "CountryTaxRule",
]
