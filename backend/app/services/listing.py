"""Listing 映射与类目模板。售价对所有有商品写权限的角色可见，不按成本权限剥离。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import PLATFORM_DISPLAY_NAMES
from app.adapters.sites import site_supported, supported_sites
from app.core.errors import AppError, ErrorCode, ParamInvalidError
from app.core.pagination import PageData, build_cursor_page, decode_cursor
from app.models.listing import LISTING_STATUSES, CategoryMapping, Listing
from app.models.platform import Shop
from app.models.product import Sku
from app.repositories.listing import CategoryMappingRepository, ListingRepository
from app.repositories.platform import ShopRepository
from app.repositories.product import SkuRepository
from app.schemas.listing import (
    CategoryMappingCreate,
    CategoryMappingPatch,
    CategoryMappingView,
    ListingCreate,
    ListingPatch,
    ListingShopOption,
    ListingView,
)
from app.schemas.shop import PlatformSiteCatalog


def read_cursor(cursor: str | None) -> int | None:
    if not cursor:
        return None
    raw = decode_cursor(cursor).get("id")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text.isascii() or not text.isdigit():
        return None
    return int(text)


def resolve_listing_status(
    *,
    current: str | None,
    requested: str | None,
    platform_product_id: str | None,
    platform_sku_id: str | None,
) -> str:
    """两个平台 ID 都在时成为已关联；从已关联清空两个 ID 时成为未上架。"""
    has_both = bool(platform_product_id and platform_sku_id)
    has_none = not platform_product_id and not platform_sku_id
    if requested is not None and requested not in LISTING_STATUSES:
        raise ParamInvalidError("状态不正确")
    if requested == "LINKED" and not has_both:
        raise AppError(
            "已关联的 Listing 需要同时填写平台商品 ID 和平台 SKU ID",
            code=ErrorCode.LISTING_STATE_INVALID,
        )
    if requested == "DRAFT" and has_both:
        raise AppError("两个平台 ID 都已填写时不能保持草稿", code=ErrorCode.LISTING_STATE_INVALID)
    if requested is not None:
        return requested
    if has_both:
        return "LINKED"
    if current == "LINKED" and not has_both:
        if has_none:
            return "UNLISTED"
        raise AppError(
            "解除关联请清空两个平台 ID，或改为未上架",
            code=ErrorCode.LISTING_STATE_INVALID,
        )
    if current is None:
        return "DRAFT"
    return current


def missing_required_attrs(template: list[dict[str, Any]], values: dict[str, str]) -> list[str]:
    missing: list[str] = []
    for item in template:
        if not item.get("required"):
            continue
        key = str(item.get("key", ""))
        if not str(values.get(key, "")).strip():
            missing.append(key)
    return missing


def resolve_selling_price(
    *,
    fields_set: set[str],
    current_price: Decimal | None,
    current_currency: str | None,
    incoming_price: Decimal | None,
    incoming_currency: str | None,
) -> tuple[Decimal | None, str | None]:
    if "price" not in fields_set and "currency" not in fields_set:
        return current_price, current_currency
    price = incoming_price if "price" in fields_set else current_price
    currency = incoming_currency if "currency" in fields_set else current_currency
    if (price is None) != (currency is None):
        raise ParamInvalidError("售价和币种需要同时填写")
    return price, currency


def assert_site(platform_code: str, site_code: str) -> None:
    if not site_supported(platform_code, site_code):
        raise ParamInvalidError("平台或站点不受支持")


def to_listing_view(
    row: Listing,
    *,
    sku: Sku | None,
    shop: Shop | None,
    template: CategoryMapping | None,
) -> ListingView:
    values = dict(row.attr_values or {})
    return ListingView(
        id=row.id,
        sku_id=row.sku_id,
        sku_code=sku.sku_code if sku is not None else "",
        shop_id=row.shop_id,
        shop_name=shop.shop_name if shop is not None else "",
        platform_code=shop.platform_code if shop is not None else "",
        site_code=shop.site_code if shop is not None else "",
        category_mapping_id=row.category_mapping_id,
        local_category_code=template.local_category_code if template is not None else None,
        template_name=template.name if template is not None else None,
        platform_product_id=row.platform_product_id,
        platform_sku_id=row.platform_sku_id,
        price=row.price,
        currency=row.currency,
        attr_values={str(key): str(value) for key, value in values.items()},
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class ListingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.listings = ListingRepository(session)
        self.templates = CategoryMappingRepository(session)
        self.skus = SkuRepository(session)
        self.shops = ShopRepository(session)

    def platform_catalog(self) -> list[PlatformSiteCatalog]:
        return [
            PlatformSiteCatalog(code=code, name=PLATFORM_DISPLAY_NAMES[code], sites=list(supported_sites(code)))
            for code in PLATFORM_DISPLAY_NAMES
        ]

    async def list_shop_options(self) -> list[ListingShopOption]:
        rows = await self.shops.list_open()
        return [
            ListingShopOption(
                id=row.id,
                shop_name=row.shop_name,
                platform_code=row.platform_code,
                site_code=row.site_code,
            )
            for row in rows
        ]

    async def list_templates(
        self,
        *,
        limit: int,
        cursor: str | None,
        platform_code: str | None,
        site_code: str | None,
        keyword: str | None,
    ) -> PageData[CategoryMappingView]:
        platform = platform_code.strip().lower() if platform_code else None
        site = site_code.strip().upper() if site_code else None
        rows = await self.templates.list_cursor(
            limit=limit,
            before_id=read_cursor(cursor),
            platform_code=platform or None,
            site_code=site or None,
            keyword=keyword.strip() if keyword else None,
        )
        items = [CategoryMappingView.model_validate(row) for row in rows]
        return build_cursor_page(items, limit)

    async def create_template(
        self,
        payload: CategoryMappingCreate,
        *,
        tenant_id: int,
        actor_id: int,
    ) -> CategoryMappingView:
        assert_site(payload.platform_code, payload.site_code)
        await self._ensure_template_free(
            platform_code=payload.platform_code,
            site_code=payload.site_code,
            local_category_code=payload.local_category_code,
        )
        row = CategoryMapping(
            tenant_id=tenant_id,
            platform_code=payload.platform_code,
            site_code=payload.site_code,
            platform_category_id=payload.platform_category_id,
            local_category_code=payload.local_category_code,
            name=payload.name,
            attrs_template=[item.model_dump() for item in payload.attrs_template],
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.templates.add(row)
        return CategoryMappingView.model_validate(row)

    async def update_template(
        self,
        template_id: int,
        payload: CategoryMappingPatch,
        *,
        actor_id: int,
    ) -> CategoryMappingView:
        row = await self.templates.get_or_404(template_id)
        fields = payload.model_fields_set
        if "platform_code" in fields and payload.platform_code:
            row.platform_code = payload.platform_code
        if "site_code" in fields and payload.site_code:
            row.site_code = payload.site_code
        if "platform_category_id" in fields and payload.platform_category_id:
            row.platform_category_id = payload.platform_category_id
        if "local_category_code" in fields and payload.local_category_code:
            row.local_category_code = payload.local_category_code
        if "name" in fields and payload.name:
            row.name = payload.name
        if "attrs_template" in fields and payload.attrs_template is not None:
            row.attrs_template = [item.model_dump() for item in payload.attrs_template]
        assert_site(row.platform_code, row.site_code)
        await self._ensure_template_free(
            platform_code=row.platform_code,
            site_code=row.site_code,
            local_category_code=row.local_category_code,
            exclude_id=row.id,
        )
        row.updated_by = actor_id
        await self.templates.flush_unique(row.local_category_code)
        return CategoryMappingView.model_validate(row)

    async def list_listings(
        self,
        *,
        limit: int,
        cursor: str | None,
        sku_id: int | None,
        shop_id: int | None,
        platform_product_id: str | None,
        status: str | None,
    ) -> PageData[ListingView]:
        if status is not None and status not in LISTING_STATUSES:
            raise ParamInvalidError("状态不正确")
        rows = await self.listings.list_cursor(
            limit=limit,
            before_id=read_cursor(cursor),
            sku_id=sku_id,
            shop_id=shop_id,
            platform_product_id=platform_product_id.strip() if platform_product_id else None,
            status=status,
        )
        items = [await self._view(row) for row in rows]
        return build_cursor_page(items, limit)

    async def get_listing(self, listing_id: int) -> ListingView:
        return await self._view(await self.listings.get_or_404(listing_id))

    async def create_listing(self, payload: ListingCreate, *, tenant_id: int, actor_id: int) -> ListingView:
        sku = await self.skus.get_or_404(payload.sku_id)
        shop = await self.shops.get_or_404(payload.shop_id)
        template = await self._template_for(payload.category_mapping_id, shop)
        self._assert_attrs(template, payload.attr_values)
        status = resolve_listing_status(
            current=None,
            requested=payload.status,
            platform_product_id=payload.platform_product_id,
            platform_sku_id=payload.platform_sku_id,
        )
        price, currency = resolve_selling_price(
            fields_set=set(payload.model_fields_set),
            current_price=None,
            current_currency=None,
            incoming_price=payload.price,
            incoming_currency=payload.currency,
        )
        await self._ensure_platform_sku_free(shop.id, payload.platform_sku_id)
        row = Listing(
            tenant_id=tenant_id,
            sku_id=sku.id,
            shop_id=shop.id,
            category_mapping_id=template.id if template is not None else None,
            platform_product_id=payload.platform_product_id,
            platform_sku_id=payload.platform_sku_id,
            price=price,
            currency=currency,
            attr_values=payload.attr_values,
            status=status,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.listings.add(row)
        return to_listing_view(row, sku=sku, shop=shop, template=template)

    async def update_listing(self, listing_id: int, payload: ListingPatch, *, actor_id: int) -> ListingView:
        row = await self.listings.get_or_404(listing_id)
        fields = payload.model_fields_set
        if "sku_id" in fields and payload.sku_id is not None:
            await self.skus.get_or_404(payload.sku_id)
            row.sku_id = payload.sku_id
        if "shop_id" in fields and payload.shop_id is not None:
            await self.shops.get_or_404(payload.shop_id)
            row.shop_id = payload.shop_id
        if "platform_product_id" in fields:
            row.platform_product_id = payload.platform_product_id
        if "platform_sku_id" in fields:
            row.platform_sku_id = payload.platform_sku_id
        if "category_mapping_id" in fields:
            row.category_mapping_id = payload.category_mapping_id
        if "attr_values" in fields:
            row.attr_values = payload.attr_values
        price, currency = resolve_selling_price(
            fields_set=set(fields),
            current_price=row.price,
            current_currency=row.currency,
            incoming_price=payload.price,
            incoming_currency=payload.currency,
        )
        row.price = price
        row.currency = currency
        shop = await self.shops.get_or_404(row.shop_id)
        template = await self._template_for(row.category_mapping_id, shop)
        values = {str(key): str(value) for key, value in dict(row.attr_values or {}).items()}
        self._assert_attrs(template, values)
        row.status = resolve_listing_status(
            current=row.status,
            requested=payload.status if "status" in fields else None,
            platform_product_id=row.platform_product_id,
            platform_sku_id=row.platform_sku_id,
        )
        await self._ensure_platform_sku_free(row.shop_id, row.platform_sku_id, exclude_id=row.id)
        row.updated_by = actor_id
        await self.listings.flush_unique(row.platform_sku_id)
        sku = await self.skus.get(row.sku_id)
        return to_listing_view(row, sku=sku, shop=shop, template=template)

    async def _template_for(self, template_id: int | None, shop: Shop) -> CategoryMapping | None:
        if template_id is None:
            return None
        template = await self.templates.get_or_404(template_id)
        if template.platform_code != shop.platform_code or template.site_code != shop.site_code:
            raise ParamInvalidError("类目模板的平台和站点需要与店铺一致")
        return template

    async def _view(self, row: Listing) -> ListingView:
        sku = await self.skus.get(row.sku_id)
        shop = await self.shops.get(row.shop_id)
        template = None
        if row.category_mapping_id is not None:
            template = await self.templates.get(row.category_mapping_id)
        return to_listing_view(row, sku=sku, shop=shop, template=template)

    async def _ensure_template_free(
        self,
        *,
        platform_code: str,
        site_code: str,
        local_category_code: str,
        exclude_id: int | None = None,
    ) -> None:
        taken = await self.templates.code_taken(
            platform_code=platform_code,
            site_code=site_code,
            local_category_code=local_category_code,
            exclude_id=exclude_id,
        )
        if taken:
            raise AppError(
                "该类目模板已存在",
                code=ErrorCode.CATEGORY_TEMPLATE_DUPLICATED,
                data={"local_category_code": local_category_code},
            )

    async def _ensure_platform_sku_free(
        self,
        shop_id: int,
        platform_sku_id: str | None,
        *,
        exclude_id: int | None = None,
    ) -> None:
        if not platform_sku_id:
            return
        taken = await self.listings.platform_sku_taken(shop_id, platform_sku_id, exclude_id=exclude_id)
        if taken:
            raise AppError(
                "该店铺的平台 SKU 已映射到本地商品",
                code=ErrorCode.LISTING_PLATFORM_SKU_TAKEN,
                data={"platform_sku_id": platform_sku_id},
            )

    @staticmethod
    def _assert_attrs(template: CategoryMapping | None, values: dict[str, str]) -> None:
        if template is None:
            return
        missing = missing_required_attrs(list(template.attrs_template or []), values)
        if missing:
            raise ParamInvalidError("必填属性未填写", data={"missing": missing})
