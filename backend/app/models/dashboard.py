"""经营看板预聚合。按日重建，不是订单流水，允许替换区间内的旧汇总。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin
from app.db.snowflake import next_snowflake_id


class DashboardShopDaily(Base, TenantMixin):
    """店铺 × 日 × 订单币种。分区键 stat_date 进入主键。"""

    __tablename__ = "dashboard_shop_daily"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=next_snowflake_id)
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    shop_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shop.id"), nullable=False)
    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    site_code: Mapped[str] = mapped_column(String(8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    book_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    order_count: Mapped[int] = mapped_column(Integer, nullable=False)
    gmv: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    book_gmv: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    book_net_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    profit_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    on_time: Mapped[int] = mapped_column(Integer, nullable=False)
    late: Mapped[int] = mapped_column(Integer, nullable=False)
    return_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ad_spend: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    ad_sales: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    ad_loss_count: Mapped[int] = mapped_column(Integer, nullable=False)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", "stat_date", name="pk_dashboard_shop_daily"),
        UniqueConstraint("tenant_id", "shop_id", "stat_date", "currency", name="uq_dashboard_shop_daily_key"),
        Index("ix_dashboard_shop_daily_tenant_id_stat_date", "tenant_id", "stat_date"),
        Index("ix_dashboard_shop_daily_tenant_id_platform_code_stat_date", "tenant_id", "platform_code", "stat_date"),
        {"postgresql_partition_by": "RANGE (stat_date)"},
    )


class DashboardInventoryDaily(Base, TenantMixin):
    """租户当天的库存健康快照。余额没有历史回放，只保留刷新当天。"""

    __tablename__ = "dashboard_inventory_daily"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=next_snowflake_id)
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    on_hand_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    stockout_sku_count: Mapped[int] = mapped_column(Integer, nullable=False)
    below_safe_sku_count: Mapped[int] = mapped_column(Integer, nullable=False)
    stale_sku_count: Mapped[int] = mapped_column(Integer, nullable=False)
    stale_amounts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", "stat_date", name="pk_dashboard_inventory_daily"),
        UniqueConstraint("tenant_id", "stat_date", name="uq_dashboard_inventory_daily_day"),
        Index("ix_dashboard_inventory_daily_tenant_id_stat_date", "tenant_id", "stat_date"),
        {"postgresql_partition_by": "RANGE (stat_date)"},
    )
