"""落地成本费用规则与计算留痕（M4-05）。

费用规则按租户配置，不写法定费率。计算记录是流水，不软删除。
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
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, TenantMixin
from app.engines.landed_cost import CHANNELS, CHARGES, FEE_CODES, LINE_CODES
from app.models.locale import CONTENT_MARKETS

FEE_ACTIVE = "ACTIVE"
FEE_DISABLED = "DISABLED"
FEE_STATUSES: tuple[str, ...] = (FEE_ACTIVE, FEE_DISABLED)
CALC_KIND = "CALC"
COMPARE_KIND = "COMPARE"
PRICING_KIND = "PRICING"
CALC_KINDS: tuple[str, ...] = (CALC_KIND, COMPARE_KIND, PRICING_KIND)

_MARKET_SQL = ", ".join(f"'{item}'" for item in CONTENT_MARKETS)
_CHANNEL_SQL = ", ".join(f"'{item}'" for item in CHANNELS)
_FEE_SQL = ", ".join(f"'{item}'" for item in FEE_CODES)
_CHARGE_SQL = ", ".join(f"'{item}'" for item in CHARGES)
_STATUS_SQL = ", ".join(f"'{item}'" for item in FEE_STATUSES)
_KIND_SQL = ", ".join(f"'{item}'" for item in CALC_KINDS)
_LINE_SQL = ", ".join(f"'{item}'" for item in LINE_CODES)


def _sql_in(name: str, sql: str) -> CheckConstraint:
    return CheckConstraint(f"{name} IN ({sql})", name=name)


class LandedCostFee(Base, PKMixin, TenantMixin, AuditMixin):
    """目的国 × 渠道 × 费用项。停用保留旧版本。"""

    __tablename__ = "landed_cost_fee"

    market: Mapped[str] = mapped_column(String(2), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    fee_code: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="*", server_default="*")
    charge: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    volumetric_divisor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=FEE_ACTIVE, server_default=FEE_ACTIVE)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    verified_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "market",
            "channel",
            "fee_code",
            "label",
            "version",
            name="uq_landed_cost_fee_version",
        ),
        _sql_in("market", _MARKET_SQL),
        _sql_in("channel", _CHANNEL_SQL),
        _sql_in("fee_code", _FEE_SQL),
        _sql_in("charge", _CHARGE_SQL),
        _sql_in("status", _STATUS_SQL),
        CheckConstraint("char_length(btrim(label)) > 0", name="label"),
        CheckConstraint("amount >= 0", name="amount"),
        CheckConstraint("charge <> 'RATE' OR amount <= 10", name="rate_cap"),
        CheckConstraint(
            "(charge = 'RATE' AND currency IS NULL) OR (charge <> 'RATE' AND currency IS NOT NULL)",
            name="currency",
        ),
        CheckConstraint("volumetric_divisor IS NULL OR volumetric_divisor >= 1", name="divisor"),
        CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="effective"),
        CheckConstraint("char_length(btrim(source)) > 0", name="source"),
        CheckConstraint("version >= 1", name="version"),
        Index("ix_landed_cost_fee_tenant_id_market", "tenant_id", "market"),
    )


class LandedCostCalc(Base, PKMixin, TenantMixin, AuditMixin):
    """一次计算或一次情景对比。参数和结果整包留下，便于以后按新规则重看。"""

    __tablename__ = "landed_cost_calc"

    sku_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    market: Mapped[str] = mapped_column(String(2), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_landed_cost_calc_sku_id_sku"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_landed_cost_calc_idempotency"),
        _sql_in("market", _MARKET_SQL),
        _sql_in("kind", _KIND_SQL),
        CheckConstraint("idempotency_key IS NULL OR char_length(btrim(idempotency_key)) > 0", name="idempotency_key"),
        Index("ix_landed_cost_calc_tenant_id_created_at", "tenant_id", "created_at"),
    )


class LandedCostLineToggle(Base, PKMixin, TenantMixin, AuditMixin):
    """十二个成本项的开关。没有记录视为开启。"""

    __tablename__ = "landed_cost_line_toggle"

    market: Mapped[str] = mapped_column(String(2), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    line_code: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "market",
            "channel",
            "line_code",
            name="uq_landed_cost_line_toggle_key",
        ),
        _sql_in("market", _MARKET_SQL),
        _sql_in("channel", _CHANNEL_SQL),
        _sql_in("line_code", _LINE_SQL),
        Index("ix_landed_cost_line_toggle_tenant_id_market", "tenant_id", "market"),
    )
