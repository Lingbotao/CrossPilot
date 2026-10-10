"""广告活动、日指标和关键词（M5-03）。

活动随平台报表更新。日指标和关键词是流水，不能删除。
V1 只读同步，没有广告写接口。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, TenantMixin
from app.db.snowflake import next_snowflake_id
from app.engines.ads import CAMPAIGN_STATUSES, CAMPAIGN_TYPES, SUGGESTION_CODES

_STATUS_SQL = ", ".join(f"'{item}'" for item in CAMPAIGN_STATUSES)
_TYPE_SQL = ", ".join(f"'{item}'" for item in CAMPAIGN_TYPES)
_SUGGESTION_SQL = ", ".join(f"'{item}'" for item in SUGGESTION_CODES)


class AdCampaign(Base, PKMixin, TenantMixin, AuditMixin):
    """平台广告活动。同一店铺的平台活动号只保留一行。"""

    __tablename__ = "ad_campaign"

    shop_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shop.id"), nullable=False)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_campaign_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    campaign_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sku.id"), nullable=True)
    platform_sku_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "shop_id",
            "platform_campaign_id",
            name="uq_ad_campaign_platform",
        ),
        CheckConstraint(f"campaign_type IN ({_TYPE_SQL})", name="campaign_type"),
        CheckConstraint(f"status IN ({_STATUS_SQL})", name="status"),
        CheckConstraint("char_length(btrim(platform_campaign_id)) > 0", name="platform_campaign_id"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name"),
        CheckConstraint("char_length(currency) = 3", name="currency"),
        Index("ix_ad_campaign_tenant_id_shop_id", "tenant_id", "shop_id"),
        Index("ix_ad_campaign_tenant_id_platform_code", "tenant_id", "platform_code"),
    )


class AdMetricDaily(Base, TenantMixin):
    """广告日指标。分区键 stat_date 必须出现在主键里。"""

    __tablename__ = "ad_metric_daily"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=next_snowflake_id)
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    campaign_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ad_campaign.id"), nullable=False)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False)
    orders: Mapped[int] = mapped_column(Integer, nullable=False)
    spend: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    sales: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    ctr: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    acos: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    roas: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    cvr: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    gross_margin: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    loss_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    suggestion_code: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")

    __table_args__ = (
        PrimaryKeyConstraint("id", "stat_date", name="pk_ad_metric_daily"),
        UniqueConstraint("tenant_id", "campaign_id", "stat_date", name="uq_ad_metric_daily_day"),
        CheckConstraint("impressions >= 0 AND clicks >= 0 AND orders >= 0", name="counts"),
        CheckConstraint("clicks <= impressions", name="clicks"),
        CheckConstraint("spend >= 0 AND sales >= 0", name="money"),
        CheckConstraint("char_length(currency) = 3", name="currency"),
        CheckConstraint(f"suggestion_code IN ({_SUGGESTION_SQL})", name="suggestion_code"),
        Index("ix_ad_metric_daily_tenant_id_stat_date", "tenant_id", "stat_date"),
        Index("ix_ad_metric_daily_tenant_id_campaign_id", "tenant_id", "campaign_id"),
        {"postgresql_partition_by": "RANGE (stat_date)"},
    )


class AdKeywordMetric(Base, TenantMixin):
    """关键词日指标。有花费且零转化的行标记为建议否定词。"""

    __tablename__ = "ad_keyword_metric"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=next_snowflake_id)
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    campaign_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ad_campaign.id"), nullable=False)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    keyword: Mapped[str] = mapped_column(String(256), nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False)
    orders: Mapped[int] = mapped_column(Integer, nullable=False)
    spend: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    sales: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    suggest_negative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    __table_args__ = (
        PrimaryKeyConstraint("id", "stat_date", name="pk_ad_keyword_metric"),
        UniqueConstraint(
            "tenant_id",
            "campaign_id",
            "keyword",
            "stat_date",
            name="uq_ad_keyword_metric_day",
        ),
        CheckConstraint("impressions >= 0 AND clicks >= 0 AND orders >= 0", name="counts"),
        CheckConstraint("spend >= 0 AND sales >= 0", name="money"),
        CheckConstraint("char_length(btrim(keyword)) > 0", name="keyword"),
        CheckConstraint("char_length(currency) = 3", name="currency"),
        Index("ix_ad_keyword_metric_tenant_id_stat_date", "tenant_id", "stat_date"),
        Index("ix_ad_keyword_metric_tenant_id_campaign_id", "tenant_id", "campaign_id"),
        {"postgresql_partition_by": "RANGE (stat_date)"},
    )


__all__ = ["AdCampaign", "AdKeywordMetric", "AdMetricDaily"]
