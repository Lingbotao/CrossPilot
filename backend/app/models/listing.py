"""Listing 映射与类目模板（M3-02）。

``listing`` 把一个本地 SKU 连到多家店铺上的平台 SKU。同一店铺的同一个平台 SKU
只能对应一个本地 SKU；多个本地 SKU 可以共用同一个平台商品 ID。
``category_mapping`` 是租户自己保存的类目模板。PRD 字段清单没有 tenant_id，
但模板按租户复用，必须隔离。两张都是主数据，可软删除。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, PKMixin, SoftDeleteMixin, TenantMixin

LISTING_STATUSES: tuple[str, ...] = ("DRAFT", "LINKED", "UNLISTED")
LISTING_STATUS_SQL = ", ".join(f"'{item}'" for item in LISTING_STATUSES)
BATCH_KINDS: tuple[str, ...] = ("PUBLISH", "PRICE")
BATCH_STATUSES: tuple[str, ...] = ("PENDING", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED")
BATCH_ITEM_STATUSES: tuple[str, ...] = ("PENDING", "SUCCEEDED", "FAILED", "SKIPPED")
_KIND_SQL = ", ".join(f"'{item}'" for item in BATCH_KINDS)
_BATCH_STATUS_SQL = ", ".join(f"'{item}'" for item in BATCH_STATUSES)
_ITEM_STATUS_SQL = ", ".join(f"'{item}'" for item in BATCH_ITEM_STATUSES)
MAX_BATCH_ITEMS = 500


class CategoryMapping(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """按平台和站点保存的类目与必填属性模板。``local_category_code`` 是租户内的复用键。"""

    __tablename__ = "category_mapping"

    platform_code: Mapped[str] = mapped_column(String(32), nullable=False)
    site_code: Mapped[str] = mapped_column(String(8), nullable=False)
    platform_category_id: Mapped[str] = mapped_column(String(128), nullable=False)
    local_category_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    attrs_template: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))

    __table_args__ = (
        Index(
            "uq_category_mapping_tenant_platform_site_code_active",
            "tenant_id",
            "platform_code",
            "site_code",
            "local_category_code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_category_mapping_tenant_id_platform_site",
            "tenant_id",
            "platform_code",
            "site_code",
        ),
    )


class Listing(Base, PKMixin, TenantMixin, AuditMixin, SoftDeleteMixin):
    """平台 Listing 映射。售价不是采购价，运营角色可以读写。"""

    __tablename__ = "listing"

    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category_mapping_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    platform_product_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    platform_sku_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    attr_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT", server_default="DRAFT")

    __table_args__ = (
        ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_listing_sku_id_sku"),
        ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_listing_shop_id_shop"),
        ForeignKeyConstraint(
            ["category_mapping_id"],
            ["category_mapping.id"],
            name="fk_listing_category_mapping_id_category_mapping",
        ),
        CheckConstraint(f"status IN ({LISTING_STATUS_SQL})", name="status"),
        CheckConstraint("price IS NULL OR price >= 0", name="price"),
        CheckConstraint(
            "(price IS NULL AND currency IS NULL) OR (price IS NOT NULL AND currency IS NOT NULL)",
            name="price_currency",
        ),
        CheckConstraint(
            "status <> 'LINKED' OR (platform_product_id IS NOT NULL AND platform_sku_id IS NOT NULL)",
            name="linked_ids",
        ),
        CheckConstraint(
            "status <> 'DRAFT' OR platform_product_id IS NULL OR platform_sku_id IS NULL",
            name="draft_ids",
        ),
        Index("ix_listing_tenant_id_sku_id", "tenant_id", "sku_id"),
        Index("ix_listing_tenant_id_shop_id", "tenant_id", "shop_id"),
        Index("ix_listing_tenant_id_platform_product_id", "tenant_id", "platform_product_id"),
        Index(
            "uq_listing_tenant_shop_platform_sku_active",
            "tenant_id",
            "shop_id",
            "platform_sku_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND platform_sku_id IS NOT NULL"),
        ),
    )


class ListingBatch(Base, PKMixin, TenantMixin, AuditMixin):
    """刊登或改价批次。操作流水，不软删除。"""

    __tablename__ = "listing_batch"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", server_default="PENDING")
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    succeeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    __table_args__ = (
        CheckConstraint(f"kind IN ({_KIND_SQL})", name="kind"),
        CheckConstraint(f"status IN ({_BATCH_STATUS_SQL})", name="status"),
        CheckConstraint("total >= 0 AND succeeded >= 0 AND failed >= 0 AND skipped >= 0", name="counts"),
        CheckConstraint("succeeded + failed + skipped <= total", name="count_sum"),
        Index("ix_listing_batch_tenant_id_created_at", "tenant_id", "created_at"),
    )


class ListingBatchItem(Base, PKMixin, TenantMixin, AuditMixin):
    """批次中的一行。改价行记下改前改后的售价。"""

    __tablename__ = "listing_batch_item"

    batch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sku_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    listing_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", server_default="PENDING")
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    price_before: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    currency_before: Mapped[str | None] = mapped_column(String(3), nullable=True)
    price_after: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    currency_after: Mapped[str | None] = mapped_column(String(3), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["batch_id"], ["listing_batch.id"], name="fk_listing_batch_item_batch_id_listing_batch"),
        ForeignKeyConstraint(["sku_id"], ["sku.id"], name="fk_listing_batch_item_sku_id_sku"),
        ForeignKeyConstraint(["shop_id"], ["shop.id"], name="fk_listing_batch_item_shop_id_shop"),
        ForeignKeyConstraint(["listing_id"], ["listing.id"], name="fk_listing_batch_item_listing_id_listing"),
        CheckConstraint(f"status IN ({_ITEM_STATUS_SQL})", name="status"),
        CheckConstraint("price_before IS NULL OR price_before >= 0", name="price_before_nonneg"),
        CheckConstraint("price_after IS NULL OR price_after >= 0", name="price_after_nonneg"),
        CheckConstraint(
            "(price_before IS NULL AND currency_before IS NULL) "
            "OR (price_before IS NOT NULL AND currency_before IS NOT NULL)",
            name="price_before",
        ),
        CheckConstraint(
            "(price_after IS NULL AND currency_after IS NULL) "
            "OR (price_after IS NOT NULL AND currency_after IS NOT NULL)",
            name="price_after",
        ),
        Index("ix_listing_batch_item_tenant_id_batch_id", "tenant_id", "batch_id"),
    )
