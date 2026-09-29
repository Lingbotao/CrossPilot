"""Listing 映射：一对多、同一平台商品下多 SKU、平台 SKU 占用、状态和必填属性。"""

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError, ErrorCode, NotFoundError, ParamInvalidError
from app.db.base import SoftDeleteMixin, TenantMixin
from app.models import TENANT_SCOPED_TABLES
from app.models.listing import LISTING_STATUSES, CategoryMapping, Listing
from app.models.platform import Shop
from app.models.product import Sku
from app.repositories.listing import _unique_violation
from app.schemas.listing import CategoryMappingCreate, ListingCreate, ListingPatch
from app.services.listing import (
    ListingService,
    missing_required_attrs,
    resolve_listing_status,
    resolve_selling_price,
)


def test_listing_tables_are_tenant_master_data() -> None:
    assert issubclass(Listing, TenantMixin)
    assert issubclass(Listing, SoftDeleteMixin)
    assert issubclass(CategoryMapping, TenantMixin)
    assert issubclass(CategoryMapping, SoftDeleteMixin)
    assert "LINKED" in LISTING_STATUSES
    assert "listing" in TENANT_SCOPED_TABLES
    assert "category_mapping" in TENANT_SCOPED_TABLES
    listing_index = next(
        item for item in Listing.__table__.indexes if item.name == "uq_listing_tenant_shop_platform_sku_active"
    )
    template_index = next(
        item
        for item in CategoryMapping.__table__.indexes
        if item.name == "uq_category_mapping_tenant_platform_site_code_active"
    )
    assert listing_index.unique is True
    assert "platform_sku_id IS NOT NULL" in str(listing_index.dialect_options["postgresql"]["where"])
    assert template_index.unique is True
    assert next(column.name for column in listing_index.columns) == "tenant_id"
    assert "can_view_cost" not in inspect.signature(ListingService.create_listing).parameters


def test_status_requires_both_platform_ids_to_link() -> None:
    assert (
        resolve_listing_status(
            current=None,
            requested=None,
            platform_product_id=None,
            platform_sku_id=None,
        )
        == "DRAFT"
    )
    assert (
        resolve_listing_status(
            current="DRAFT",
            requested=None,
            platform_product_id="P1",
            platform_sku_id="S1",
        )
        == "LINKED"
    )
    assert (
        resolve_listing_status(
            current="LINKED",
            requested=None,
            platform_product_id=None,
            platform_sku_id=None,
        )
        == "UNLISTED"
    )
    with pytest.raises(AppError) as missing:
        resolve_listing_status(current=None, requested="LINKED", platform_product_id="P1", platform_sku_id=None)
    assert missing.value.code == ErrorCode.LISTING_STATE_INVALID
    with pytest.raises(AppError) as draft:
        resolve_listing_status(current="LINKED", requested="DRAFT", platform_product_id="P1", platform_sku_id="S1")
    assert draft.value.code == ErrorCode.LISTING_STATE_INVALID
    with pytest.raises(AppError) as partial:
        resolve_listing_status(current="LINKED", requested=None, platform_product_id="P1", platform_sku_id=None)
    assert partial.value.code == ErrorCode.LISTING_STATE_INVALID


def test_required_attrs_and_selling_price() -> None:
    template = [
        {"key": "color", "label": "颜色", "required": True},
        {"key": "size", "label": "尺码", "required": False},
    ]
    assert missing_required_attrs(template, {"color": "红"}) == []
    assert missing_required_attrs(template, {"color": "  "}) == ["color"]
    price, currency = resolve_selling_price(
        fields_set={"price", "currency"},
        current_price=None,
        current_currency=None,
        incoming_price=Decimal("19.900000"),
        incoming_currency="SGD",
    )
    assert price == Decimal("19.900000")
    assert currency == "SGD"
    kept = resolve_selling_price(
        fields_set=set(),
        current_price=Decimal("1"),
        current_currency="USD",
        incoming_price=None,
        incoming_currency=None,
    )
    assert kept == (Decimal("1"), "USD")
    with pytest.raises(ParamInvalidError):
        resolve_selling_price(
            fields_set={"price"},
            current_price=None,
            current_currency=None,
            incoming_price=Decimal("1"),
            incoming_currency=None,
        )


def test_listing_schema_rejects_float_price_and_bad_codes() -> None:
    with pytest.raises(ValidationError):
        ListingCreate.model_validate({"sku_id": "21", "shop_id": "31", "price": 1.5, "currency": "SGD"})
    with pytest.raises(ValidationError):
        ListingCreate.model_validate({"sku_id": "21", "shop_id": "31", "price": "19.9"})
    with pytest.raises(ValidationError):
        CategoryMappingCreate.model_validate(
            {
                "platform_code": "shopee",
                "site_code": "sg",
                "platform_category_id": "1001",
                "local_category_code": "tee",
                "name": "T恤",
                "attrs_template": [
                    {"key": "color", "label": "颜色", "required": True},
                    {"key": "color", "label": "颜色2", "required": False},
                ],
            }
        )
    created = ListingCreate.model_validate({"sku_id": "21", "shop_id": "31", "price": "19.900000", "currency": "sgd"})
    assert created.price == Decimal("19.900000")
    assert created.currency == "SGD"


def test_unique_violation_matches_the_active_index() -> None:
    class Orig(Exception):
        pgcode = "23505"

    hit = IntegrityError("insert", {}, Orig("uq_listing_tenant_shop_platform_sku_active"))
    miss = IntegrityError("insert", {}, Orig("uq_sku_tenant_id_sku_code_active"))
    assert _unique_violation(hit, "uq_listing_tenant_shop_platform_sku_active") is True
    assert _unique_violation(miss, "uq_listing_tenant_shop_platform_sku_active") is False


def _sku(entity_id: int, code: str) -> Sku:
    now = datetime.now(UTC)
    return Sku(
        id=entity_id,
        tenant_id=9,
        spu_id=11,
        sku_code=code,
        spec_attrs={},
        weight_g=Decimal("10"),
        length_cm=Decimal("2"),
        width_cm=Decimal("3"),
        height_cm=Decimal("4"),
        created_at=now,
        updated_at=now,
    )


def _shop(entity_id: int, name: str, site: str) -> Shop:
    now = datetime.now(UTC)
    return Shop(
        id=entity_id,
        tenant_id=9,
        platform_code="shopee",
        site_code=site,
        shop_name=name,
        platform_shop_id=f"seller-{entity_id}",
        status=1,
        created_at=now,
        updated_at=now,
    )


def _service() -> tuple[ListingService, list[Listing], dict[int, CategoryMapping]]:
    session = MagicMock()
    session.flush = AsyncMock()
    service = ListingService(session)
    skus = {21: _sku(21, "TEE-RED"), 22: _sku(22, "TEE-BLUE")}
    shops = {31: _shop(31, "虾皮新加坡", "SG"), 32: _shop(32, "虾皮马来", "MY")}
    templates: dict[int, CategoryMapping] = {}
    listings: list[Listing] = []

    async def add_template(obj: CategoryMapping) -> CategoryMapping:
        now = datetime.now(UTC)
        obj.id = 40 + len(templates)
        obj.created_at = now
        obj.updated_at = now
        templates[obj.id] = obj
        return obj

    async def add_listing(obj: Listing) -> Listing:
        now = datetime.now(UTC)
        obj.id = 50 + len(listings)
        obj.created_at = now
        obj.updated_at = now
        listings.append(obj)
        return obj

    async def code_taken(**kwargs: object) -> bool:
        for row in templates.values():
            if row.id == kwargs["exclude_id"]:
                continue
            if (
                row.platform_code == kwargs["platform_code"]
                and row.site_code == kwargs["site_code"]
                and row.local_category_code == kwargs["local_category_code"]
            ):
                return True
        return False

    async def platform_sku_taken(shop_id: int, platform_sku_id: str, *, exclude_id: int | None = None) -> bool:
        for row in listings:
            if row.id == exclude_id:
                continue
            if row.shop_id == shop_id and row.platform_sku_id == platform_sku_id:
                return True
        return False

    async def list_cursor(**kwargs: object) -> list[Listing]:
        rows = list(listings)
        if kwargs["sku_id"] is not None:
            rows = [row for row in rows if row.sku_id == kwargs["sku_id"]]
        if kwargs["platform_product_id"]:
            rows = [row for row in rows if row.platform_product_id == kwargs["platform_product_id"]]
        return rows

    async def get_sku_or_404(entity_id: int) -> Sku:
        found = skus.get(entity_id)
        if found is None:
            raise NotFoundError()
        return found

    async def get_shop_or_404(entity_id: int) -> Shop:
        found = shops.get(entity_id)
        if found is None:
            raise NotFoundError()
        return found

    async def get_listing_or_404(entity_id: int) -> Listing:
        for row in listings:
            if row.id == entity_id:
                return row
        raise NotFoundError()

    service.templates = MagicMock()
    service.listings = MagicMock()
    service.skus = MagicMock()
    service.shops = MagicMock()
    service.templates.add = AsyncMock(side_effect=add_template)
    service.templates.get_or_404 = AsyncMock(side_effect=lambda entity_id: templates[entity_id])
    service.templates.get = AsyncMock(side_effect=lambda entity_id: templates.get(entity_id))
    service.templates.code_taken = AsyncMock(side_effect=code_taken)
    service.templates.flush_unique = AsyncMock()
    service.listings.add = AsyncMock(side_effect=add_listing)
    service.listings.get_or_404 = AsyncMock(side_effect=get_listing_or_404)
    service.listings.platform_sku_taken = AsyncMock(side_effect=platform_sku_taken)
    service.listings.flush_unique = AsyncMock()
    service.listings.list_cursor = AsyncMock(side_effect=list_cursor)
    service.skus.get_or_404 = AsyncMock(side_effect=get_sku_or_404)
    service.skus.get = AsyncMock(side_effect=lambda entity_id: skus.get(entity_id))
    service.shops.get_or_404 = AsyncMock(side_effect=get_shop_or_404)
    service.shops.get = AsyncMock(side_effect=lambda entity_id: shops.get(entity_id))
    return service, listings, templates


def _template_payload() -> CategoryMappingCreate:
    return CategoryMappingCreate.model_validate(
        {
            "platform_code": "Shopee",
            "site_code": "sg",
            "platform_category_id": "10086",
            "local_category_code": "tee",
            "name": "T恤",
            "attrs_template": [{"key": "color", "label": "颜色", "required": True}],
        }
    )


async def test_one_sku_maps_to_many_shops_and_keeps_selling_price() -> None:
    service, listings, _templates = _service()
    template = await service.create_template(_template_payload(), tenant_id=9, actor_id=3)
    assert template.platform_code == "shopee"
    assert template.site_code == "SG"
    first = await service.create_listing(
        ListingCreate.model_validate(
            {
                "sku_id": "21",
                "shop_id": "31",
                "category_mapping_id": template.id,
                "platform_product_id": "ITEM-1",
                "platform_sku_id": "SKU-SG",
                "price": "19.900000",
                "currency": "SGD",
                "attr_values": {"color": "红"},
            }
        ),
        tenant_id=9,
        actor_id=3,
    )
    second = await service.create_listing(
        ListingCreate.model_validate(
            {
                "sku_id": "21",
                "shop_id": "32",
                "platform_product_id": "ITEM-1",
                "platform_sku_id": "SKU-MY",
                "price": "39.000000",
                "currency": "MYR",
            }
        ),
        tenant_id=9,
        actor_id=3,
    )
    assert first.status == "LINKED"
    assert second.sku_id == first.sku_id
    assert second.shop_id != first.shop_id
    dumped = first.model_dump(mode="json")
    assert dumped["price"] == "19.900000"
    assert dumped["currency"] == "SGD"
    page = await service.list_listings(
        limit=20,
        cursor=None,
        sku_id=21,
        shop_id=None,
        platform_product_id=None,
        status=None,
    )
    assert [item.shop_id for item in page.items] == [row.shop_id for row in listings]
    assert {item.sku_code for item in page.items} == {"TEE-RED"}


async def test_many_skus_share_one_platform_product_and_duplicate_sku_is_rejected() -> None:
    service, _listings, _templates = _service()
    await service.create_listing(
        ListingCreate.model_validate(
            {"sku_id": "21", "shop_id": "31", "platform_product_id": "ITEM-9", "platform_sku_id": "RED"}
        ),
        tenant_id=9,
        actor_id=3,
    )
    blue = await service.create_listing(
        ListingCreate.model_validate(
            {"sku_id": "22", "shop_id": "31", "platform_product_id": "ITEM-9", "platform_sku_id": "BLUE"}
        ),
        tenant_id=9,
        actor_id=3,
    )
    assert blue.platform_product_id == "ITEM-9"
    assert blue.sku_code == "TEE-BLUE"
    page = await service.list_listings(
        limit=20,
        cursor=None,
        sku_id=None,
        shop_id=None,
        platform_product_id="ITEM-9",
        status=None,
    )
    assert {item.sku_code for item in page.items} == {"TEE-RED", "TEE-BLUE"}
    with pytest.raises(AppError) as captured:
        await service.create_listing(
            ListingCreate.model_validate(
                {"sku_id": "22", "shop_id": "31", "platform_product_id": "ITEM-9", "platform_sku_id": "RED"}
            ),
            tenant_id=9,
            actor_id=3,
        )
    assert captured.value.code == ErrorCode.LISTING_PLATFORM_SKU_TAKEN


async def test_template_rules_block_missing_attrs_duplicates_and_foreign_ids() -> None:
    service, listings, _templates = _service()
    template = await service.create_template(_template_payload(), tenant_id=9, actor_id=3)
    with pytest.raises(AppError) as duplicated:
        await service.create_template(_template_payload(), tenant_id=9, actor_id=3)
    assert duplicated.value.code == ErrorCode.CATEGORY_TEMPLATE_DUPLICATED
    with pytest.raises(ParamInvalidError):
        await service.create_template(
            CategoryMappingCreate.model_validate(
                {
                    "platform_code": "shopee",
                    "site_code": "ZZ",
                    "platform_category_id": "1",
                    "local_category_code": "other",
                    "name": "无效站点",
                }
            ),
            tenant_id=9,
            actor_id=3,
        )
    with pytest.raises(ParamInvalidError) as missing:
        await service.create_listing(
            ListingCreate.model_validate(
                {
                    "sku_id": "21",
                    "shop_id": "31",
                    "category_mapping_id": template.id,
                    "attr_values": {},
                }
            ),
            tenant_id=9,
            actor_id=3,
        )
    assert missing.value.code == ErrorCode.PARAM_INVALID
    with pytest.raises(NotFoundError):
        await service.create_listing(
            ListingCreate.model_validate({"sku_id": "999", "shop_id": "31"}),
            tenant_id=9,
            actor_id=3,
        )
    created = await service.create_listing(
        ListingCreate.model_validate(
            {
                "sku_id": "21",
                "shop_id": "31",
                "platform_product_id": "ITEM-1",
                "platform_sku_id": "SKU-SG",
            }
        ),
        tenant_id=9,
        actor_id=3,
    )
    updated = await service.update_listing(
        int(created.id),
        ListingPatch.model_validate({"platform_product_id": None, "platform_sku_id": None}),
        actor_id=3,
    )
    assert updated.status == "UNLISTED"
    assert listings[0].status == "UNLISTED"
    with pytest.raises(AppError) as linked:
        await service.update_listing(
            int(created.id),
            ListingPatch.model_validate({"status": "LINKED", "platform_product_id": "ONLY"}),
            actor_id=3,
        )
    assert linked.value.code == ErrorCode.LISTING_STATE_INVALID
