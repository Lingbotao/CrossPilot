"""商品主数据（M3-01）。

``spu`` / ``sku`` 是可软删除的主数据，不是流水。重量和尺寸必填，供 M4 落地成本读取。
采购价可空；没有采购价时币种也必须为空。SKU 编码在未删除行里租户内唯一。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKeyConstraint, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, SoftDeleteMixin, TenantMixin

PRODUCT_STATUSES: tuple[str, ...] = (
    "DRAFT",
    "ON_SALE",
    "STOPPED",
    "OUT_OF_STOCK",
    "VIOLATION_OFF",
)
PRODUCT_STATUS_SQL = ", ".join(f"'{item}'" for item in PRODUCT_STATUSES)
IMAGE_TYPES: tuple[str, ...] = ("MAIN", "GALLERY", "APLUS")
IMAGE_TYPE_SQL = ", ".join(f"'{item}'" for item in IMAGE_TYPES)
MAX_SKUS_PER_SPU = 100


class Spu(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """标准商品单元。状态变更记入审计日志，不另建流水表。"""

    __tablename__ = "spu"

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    material: Mapped[str | None] = mapped_column(String(128), nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(256), nullable=True)
    category_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT", server_default="DRAFT")

    __table_args__ = (
        CheckConstraint(f"status IN ({PRODUCT_STATUS_SQL})", name="status"),
        CheckConstraint(
            "category_code IS NULL OR char_length(category_code) > 0",
            name="ck_spu_category_code",
        ),
        Index("ix_spu_tenant_id_status", "tenant_id", "status"),
    )


class Sku(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """变体。重量单位是克，尺寸单位是厘米，都用 NUMERIC，禁止 float。"""

    __tablename__ = "sku"

    spu_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku_code: Mapped[str] = mapped_column(String(64), nullable=False)
    barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    spec_attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    weight_g: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    length_cm: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    width_cm: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    height_cm: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["spu_id"], ["spu.id"], name="fk_sku_spu_id_spu"),
        CheckConstraint(
            "weight_g > 0 AND length_cm > 0 AND width_cm > 0 AND height_cm > 0",
            name="measures",
        ),
        CheckConstraint("purchase_price IS NULL OR purchase_price >= 0", name="purchase_price"),
        CheckConstraint(
            "(purchase_price IS NULL AND currency IS NULL) OR (purchase_price IS NOT NULL AND currency IS NOT NULL)",
            name="purchase_currency",
        ),
        Index(
            "uq_sku_tenant_id_sku_code_active",
            "tenant_id",
            "sku_code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_sku_tenant_id_spu_id", "tenant_id", "spu_id"),
    )


class ProductImage(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """商品图片。尺寸和白底不合规时仍保存，并在 platform_compliance 里给出提示。"""

    __tablename__ = "product_image"

    spu_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    image_type: Mapped[str] = mapped_column(String(16), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    width_px: Mapped[int] = mapped_column(Integer, nullable=False)
    height_px: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    white_background: Mapped[bool] = mapped_column(Boolean, nullable=False)
    platform_compliance: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))

    __table_args__ = (
        ForeignKeyConstraint(["spu_id"], ["spu.id"], name="fk_product_image_spu_id_spu"),
        ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_product_image_sku_id_sku"),
        CheckConstraint(f"image_type IN ({IMAGE_TYPE_SQL})", name="image_type"),
        CheckConstraint("width_px > 0 AND height_px > 0 AND byte_size > 0 AND sort >= 0", name="measures"),
        Index("ix_product_image_tenant_id_spu_id", "tenant_id", "spu_id"),
    )
