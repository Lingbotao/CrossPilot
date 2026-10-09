"""多语言文案、术语库和敏感词（M3-05）。

三种都是可软删除的主数据。``listing_content`` 按 Listing × 语言各存一份，
质量状态只能走未翻译、机翻草稿、已校对、已发布。术语和敏感词按租户隔离。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, SoftDeleteMixin, TenantMixin

CONTENT_LANGUAGES: tuple[str, ...] = (
    "zh-CN",
    "zh-TW",
    "en",
    "id",
    "th",
    "vi",
    "ms",
    "es",
    "pt",
    "de",
    "fr",
    "it",
    "ja",
)
CONTENT_MARKETS: tuple[str, ...] = (
    "US",
    "CA",
    "MX",
    "UK",
    "GB",
    "DE",
    "FR",
    "IT",
    "ES",
    "JP",
    "AU",
    "SG",
    "MY",
    "TH",
    "ID",
    "VN",
    "PH",
    "TW",
    "BR",
)
QUALITY_UNTRANSLATED = "UNTRANSLATED"
QUALITY_MT_DRAFT = "MT_DRAFT"
QUALITY_REVIEWED = "REVIEWED"
QUALITY_PUBLISHED = "PUBLISHED"
QUALITY_STATUSES: tuple[str, ...] = (
    QUALITY_UNTRANSLATED,
    QUALITY_MT_DRAFT,
    QUALITY_REVIEWED,
    QUALITY_PUBLISHED,
)
_LANG_SQL = ", ".join(f"'{item}'" for item in CONTENT_LANGUAGES)
_MARKET_SQL = ", ".join(f"'{item}'" for item in CONTENT_MARKETS)
_QUALITY_SQL = ", ".join(f"'{item}'" for item in QUALITY_STATUSES)


class ListingContent(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """一个 Listing 的一种语言。语言之间互不覆盖。"""

    __tablename__ = "listing_content"

    listing_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lang: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    bullet_points: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    quality_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=QUALITY_UNTRANSLATED,
        server_default=QUALITY_UNTRANSLATED,
    )

    __table_args__ = (
        ForeignKeyConstraint(["listing_id"], ["listing.id"], name="fk_listing_content_listing_id_listing"),
        CheckConstraint(f"lang IN ({_LANG_SQL})", name="lang"),
        CheckConstraint(f"quality_status IN ({_QUALITY_SQL})", name="quality_status"),
        CheckConstraint("jsonb_typeof(bullet_points) = 'array'", name="bullet_points_array"),
        Index("ix_listing_content_tenant_id_listing_id", "tenant_id", "listing_id"),
        Index("ix_listing_content_tenant_id_quality_status", "tenant_id", "quality_status"),
        Index(
            "uq_listing_content_lang_active",
            "tenant_id",
            "listing_id",
            "lang",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class GlossaryTerm(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """品牌词和专有名词的统一译法。生成草稿时按源语言强制替换。"""

    __tablename__ = "glossary_term"

    source_lang: Mapped[str] = mapped_column(String(16), nullable=False)
    source_term: Mapped[str] = mapped_column(String(128), nullable=False)
    target_lang: Mapped[str] = mapped_column(String(16), nullable=False)
    target_term: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        CheckConstraint(f"source_lang IN ({_LANG_SQL})", name="source_lang"),
        CheckConstraint(f"target_lang IN ({_LANG_SQL})", name="target_lang"),
        CheckConstraint("char_length(source_term) > 0 AND char_length(target_term) > 0", name="terms"),
        Index("ix_glossary_term_tenant_id_target_lang", "tenant_id", "target_lang"),
        Index(
            "uq_glossary_term_active",
            "tenant_id",
            "source_lang",
            "target_lang",
            text("lower(source_term)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class SensitiveTerm(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """按市场维护的文化禁忌词。命中后提示替换，不在本模块拦截发布。"""

    __tablename__ = "sensitive_term"

    market: Mapped[str] = mapped_column(String(8), nullable=False)
    lang: Mapped[str] = mapped_column(String(16), nullable=False)
    keyword: Mapped[str] = mapped_column(String(128), nullable=False)
    suggest_replacement: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        CheckConstraint(f"market IN ({_MARKET_SQL})", name="market"),
        CheckConstraint(f"lang IN ({_LANG_SQL})", name="lang"),
        CheckConstraint("char_length(keyword) > 0", name="keyword"),
        Index("ix_sensitive_term_tenant_id_market_lang", "tenant_id", "market", "lang"),
        Index(
            "uq_sensitive_term_active",
            "tenant_id",
            "market",
            "lang",
            text("lower(keyword)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
