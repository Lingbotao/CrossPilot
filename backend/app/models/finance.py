"""汇率与 SKU 日利润（M4-07）。

汇率按租户手工维护，口径分下单、结算、记账。日利润按统计日分区，汇兑损益单列。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, TenantMixin
from app.db.snowflake import next_snowflake_id

ORDER_BASIS = "ORDER"
SETTLEMENT_BASIS = "SETTLEMENT"
BOOK_BASIS = "BOOK"
RATE_BASES: tuple[str, ...] = (ORDER_BASIS, SETTLEMENT_BASIS, BOOK_BASIS)
_BASIS_SQL = ", ".join(f"'{item}'" for item in RATE_BASES)


class ExchangeRate(Base, PKMixin, TenantMixin, AuditMixin):
    """1 单位 base_currency = rate 单位 quote_currency。锁定后不能覆盖。"""

    __tablename__ = "exchange_rate"

    base_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    basis: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_on: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "base_currency",
            "quote_currency",
            "basis",
            "effective_on",
            name="uq_exchange_rate_key",
        ),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_exchange_rate_idempotency"),
        CheckConstraint(f"basis IN ({_BASIS_SQL})", name="basis"),
        CheckConstraint("rate > 0", name="rate"),
        CheckConstraint("base_currency <> quote_currency", name="pair"),
        CheckConstraint("char_length(btrim(source)) > 0", name="source"),
        CheckConstraint(
            "idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0",
            name="idempotency_key",
        ),
        Index(
            "ix_exchange_rate_tenant_id_pair",
            "tenant_id",
            "base_currency",
            "quote_currency",
            "basis",
            "effective_on",
        ),
    )


class SkuProfitDaily(Base, TenantMixin):
    """SKU × 店铺 × 日 × 订单币种。分区键 stat_date 必须出现在主键里。"""

    __tablename__ = "sku_profit_daily"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=next_snowflake_id)
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    book_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    revenue: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    cost_total: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    net_margin: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    book_revenue: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    book_cost_total: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    book_net_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    fx_gain: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    fx_formula: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    fx_source: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", "stat_date", name="pk_sku_profit_daily"),
        UniqueConstraint(
            "tenant_id",
            "sku_id",
            "shop_id",
            "stat_date",
            "currency",
            name="uq_sku_profit_daily_key",
        ),
        CheckConstraint("quantity > 0", name="quantity"),
        CheckConstraint("revenue >= 0", name="revenue"),
        Index("ix_sku_profit_daily_tenant_id_stat_date", "tenant_id", "stat_date"),
        {"postgresql_partition_by": "RANGE (stat_date)"},
    )


__all__ = [
    "BOOK_BASIS",
    "ExchangeRate",
    "ORDER_BASIS",
    "RATE_BASES",
    "SETTLEMENT_BASIS",
    "SkuProfitDaily",
]
