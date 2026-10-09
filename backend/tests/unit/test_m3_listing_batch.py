"""批量刊登与批量改价：500 条上限、部分成功、已关联跳过、改价超过 20% 要确认。"""

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.adapters.amazon.adapter import AmazonAdapter
from app.adapters.base import CredentialView, PriceUpdate, PublishResult, UnifiedProduct
from app.adapters.errors import AdapterError, RetryDecision
from app.adapters.lazada.adapter import LazadaAdapter
from app.adapters.shopee.adapter import ShopeeAdapter
from app.adapters.tiktok.adapter import TikTokAdapter
from app.adapters.transport import FixtureTransport
from app.core.errors import AppError, ErrorCode, NotFoundError
from app.db.base import SoftDeleteMixin, TenantMixin
from app.models import TENANT_SCOPED_TABLES
from app.models.listing import Listing, ListingBatch, ListingBatchItem
from app.models.platform import Shop
from app.models.product import Sku, Spu
from app.schemas.listing_batch import PriceBatchCreate, PublishBatchCreate
from app.services.listing_batch import (
    PRICE_CHANGE_CONFIRM_RATIO,
    ListingBatchService,
    needs_price_confirm,
)
from app.sync_engine.queues import task_queue
from app.tasks.batch import publish_listings, update_prices


def test_batch_tables_are_tenant_ledgers() -> None:
    assert issubclass(ListingBatch, TenantMixin)
    assert issubclass(ListingBatchItem, TenantMixin)
    assert not issubclass(ListingBatch, SoftDeleteMixin)
    assert not issubclass(ListingBatchItem, SoftDeleteMixin)
    assert "listing_batch" in TENANT_SCOPED_TABLES
    assert "listing_batch_item" in TENANT_SCOPED_TABLES
    index = next(
        item for item in ListingBatch.__table__.indexes if item.name == "ix_listing_batch_tenant_id_created_at"
    )
    assert next(column.name for column in index.columns) == "tenant_id"
    assert "can_view_cost" not in inspect.signature(ListingBatchService.accept_publish).parameters
    assert "tenant_id" in inspect.signature(publish_listings).parameters
    assert "tenant_id" in inspect.signature(update_prices).parameters
    assert task_queue("batch.publish_listings") == "batch"
    assert task_queue("batch.update_prices") == "batch"


def test_price_confirm_threshold_is_strictly_above_twenty_percent() -> None:
    assert Decimal("0.20") == PRICE_CHANGE_CONFIRM_RATIO
    assert needs_price_confirm(Decimal("10"), Decimal("12")) is False
    assert needs_price_confirm(Decimal("10"), Decimal("8")) is False
    assert needs_price_confirm(Decimal("10"), Decimal("12.01")) is True
    assert needs_price_confirm(Decimal("10"), Decimal("7.99")) is True
    assert needs_price_confirm(None, Decimal("1")) is True
    assert needs_price_confirm(Decimal("0"), Decimal("1")) is True


def test_batch_schema_rejects_float_price() -> None:
    with pytest.raises(ValidationError):
        PublishBatchCreate.model_validate({"sku_ids": ["21"], "shop_ids": ["31"], "price": 1.5, "currency": "SGD"})
    created = PublishBatchCreate.model_validate(
        {"sku_ids": ["21"], "shop_ids": ["31"], "price": "19.900000", "currency": "sgd"}
    )
    assert created.price == Decimal("19.900000")
    assert created.currency == "SGD"


class _Catalog:
    def __init__(self, fail_shop: int | None = None) -> None:
        self.fail_shop = fail_shop
        self.prices: list[tuple[int, Decimal]] = []

    async def publish(self, shop: Shop, *, title: str, sku_code: str, price: Decimal, currency: str) -> PublishResult:
        del title, price, currency
        if shop.id == self.fail_shop:
            raise AdapterError("平台拒绝刊登", platform=shop.platform_code, decision=RetryDecision.FAIL_FAST)
        return PublishResult(
            platform_product_id=f"P-{shop.id}-{sku_code}",
            platform_sku_id=f"S-{shop.id}-{sku_code}",
            raw={},
        )

    async def update_price(self, shop: Shop, *, platform_sku_id: str, price: Decimal, currency: str) -> None:
        del platform_sku_id, currency
        if shop.id == self.fail_shop:
            raise AdapterError("平台拒绝改价", platform=shop.platform_code, decision=RetryDecision.FAIL_FAST)
        self.prices.append((shop.id, price))


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


def _shop(entity_id: int, name: str) -> Shop:
    now = datetime.now(UTC)
    return Shop(
        id=entity_id,
        tenant_id=9,
        platform_code="shopee",
        site_code="SG",
        shop_name=name,
        platform_shop_id=f"seller-{entity_id}",
        status=1,
        created_at=now,
        updated_at=now,
    )


def _stamp(obj: Listing | ListingBatch | ListingBatchItem, entity_id: int) -> None:
    now = datetime.now(UTC)
    obj.id = entity_id
    obj.created_at = now
    obj.updated_at = now


def _service(
    fail_shop: int | None = None,
) -> tuple[ListingBatchService, list[Listing], list[ListingBatch], _Catalog]:
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    catalog = _Catalog(fail_shop)
    service = ListingBatchService(session, catalog=catalog)
    skus = {21: _sku(21, "TEE-RED"), 22: _sku(22, "TEE-BLUE")}
    shops = {31: _shop(31, "新加坡店"), 32: _shop(32, "马来店")}
    spu = Spu(
        id=11, tenant_id=9, title="T恤", status="DRAFT", created_at=datetime.now(UTC), updated_at=datetime.now(UTC)
    )
    listings: list[Listing] = []
    batches: list[ListingBatch] = []
    items: list[ListingBatchItem] = []

    async def add_listing(obj: Listing) -> Listing:
        _stamp(obj, 50 + len(listings))
        listings.append(obj)
        return obj

    async def add_batch(obj: ListingBatch) -> ListingBatch:
        _stamp(obj, 80 + len(batches))
        batches.append(obj)
        return obj

    async def add_item(obj: ListingBatchItem) -> ListingBatchItem:
        _stamp(obj, 90 + len(items))
        items.append(obj)
        return obj

    async def get_batch(entity_id: int) -> ListingBatch:
        for row in batches:
            if row.id == entity_id:
                return row
        raise NotFoundError()

    async def get_listing(entity_id: int) -> Listing:
        for row in listings:
            if row.id == entity_id:
                return row
        raise NotFoundError()

    async def get_item(entity_id: int) -> ListingBatchItem:
        for row in items:
            if row.id == entity_id:
                return row
        raise NotFoundError()

    service.listings = MagicMock()
    service.batches = MagicMock()
    service.items = MagicMock()
    service.skus = MagicMock()
    service.shops = MagicMock()
    service.spus = MagicMock()
    service.listings.add = AsyncMock(side_effect=add_listing)
    service.listings.get_or_404 = AsyncMock(side_effect=get_listing)
    service.listings.flush_unique = AsyncMock()
    service.listings.linked_pair = AsyncMock(
        side_effect=lambda sku_id, shop_id: next(
            (
                row
                for row in reversed(listings)
                if row.sku_id == sku_id and row.shop_id == shop_id and row.status == "LINKED"
            ),
            None,
        )
    )
    service.listings.open_pair = AsyncMock(
        side_effect=lambda sku_id, shop_id: next(
            (
                row
                for row in reversed(listings)
                if row.sku_id == sku_id and row.shop_id == shop_id and row.status != "LINKED"
            ),
            None,
        )
    )
    service.listings.get_many = AsyncMock(side_effect=lambda ids: [row for row in listings if row.id in ids])
    service.batches.add = AsyncMock(side_effect=add_batch)
    service.batches.get_or_404 = AsyncMock(side_effect=get_batch)
    service.items.add = AsyncMock(side_effect=add_item)
    service.items.get_or_404 = AsyncMock(side_effect=get_item)
    service.items.list_for_batch = AsyncMock(
        side_effect=lambda batch_id: [row for row in items if row.batch_id == batch_id]
    )
    service.skus.get_or_404 = AsyncMock(
        side_effect=lambda entity_id: skus[entity_id] if entity_id in skus else (_ for _ in ()).throw(NotFoundError())
    )
    service.shops.get_or_404 = AsyncMock(
        side_effect=lambda entity_id: shops[entity_id] if entity_id in shops else (_ for _ in ()).throw(NotFoundError())
    )
    service.spus.get_or_404 = AsyncMock(return_value=spu)
    service.contents = MagicMock()
    service.contents.get_active = AsyncMock(return_value=SimpleNamespace(quality_status="PUBLISHED", title="T恤"))
    return service, listings, batches, catalog


def _seed(listings: list[Listing], **kwargs: object) -> Listing:
    now = datetime.now(UTC)
    status = str(kwargs["status"])
    row = Listing(
        id=int(kwargs["listing_id"]),  # type: ignore[arg-type]
        tenant_id=9,
        sku_id=int(kwargs["sku_id"]),  # type: ignore[arg-type]
        shop_id=int(kwargs["shop_id"]),  # type: ignore[arg-type]
        price=kwargs["price"],  # type: ignore[arg-type]
        currency=kwargs["currency"],  # type: ignore[arg-type]
        attr_values={},
        status=status,
        platform_product_id="ITEM-1" if status == "LINKED" else None,
        platform_sku_id=kwargs.get("platform_sku_id") if status == "LINKED" else None,  # type: ignore[arg-type]
        created_at=now,
        updated_at=now,
    )
    listings.append(row)
    return row


def _publish(sku_ids: list[str], shop_ids: list[str]) -> PublishBatchCreate:
    return PublishBatchCreate.model_validate(
        {"sku_ids": sku_ids, "shop_ids": shop_ids, "price": "19.900000", "currency": "SGD"}
    )


async def test_more_than_500_pairs_are_rejected() -> None:
    service, _listings, batches, _catalog = _service()
    with pytest.raises(AppError) as exc:
        await service.accept_publish(
            _publish([str(item) for item in range(1, 502)], ["31"]),
            tenant_id=9,
            actor_id=3,
        )
    assert exc.value.code == ErrorCode.LISTING_BATCH_TOO_LARGE
    assert batches == []


async def test_one_sku_publishes_to_two_shops_and_skips_an_existing_link() -> None:
    service, listings, _batches, catalog = _service()
    _seed(
        listings,
        listing_id=41,
        sku_id=21,
        shop_id=31,
        price=Decimal("10"),
        currency="SGD",
        status="LINKED",
        platform_sku_id="ALREADY",
    )
    accepted = await service.accept_publish(_publish(["21", "21"], ["31", "32"]), tenant_id=9, actor_id=3)
    assert accepted.total == 2
    assert accepted.skipped == 1
    finished = await service.run_publish(accepted.id)
    assert finished.status == "SUCCEEDED"
    assert finished.succeeded == 1
    linked = [row for row in listings if row.status == "LINKED"]
    assert len(linked) == 2
    created = next(row for row in listings if row.shop_id == 32)
    assert created.platform_product_id == "P-32-TEE-RED"
    assert created.platform_sku_id == "S-32-TEE-RED"
    assert created.price == Decimal("19.900000")
    assert catalog.prices == []


async def test_unreviewed_language_blocks_shop_publish() -> None:
    service, listings, _batches, catalog = _service()
    service.contents.get_active = AsyncMock(return_value=SimpleNamespace(quality_status="MT_DRAFT", title="Draft"))
    accepted = await service.accept_publish(_publish(["21"], ["31"]), tenant_id=9, actor_id=3)
    finished = await service.run_publish(accepted.id)
    assert finished.status == "FAILED"
    assert finished.failed == 1
    assert listings[0].status == "DRAFT"
    assert listings[0].platform_product_id is None
    assert catalog.prices == []
    assert finished.items[0].error_message == "该站点语言（en）尚未人工校对并发布"


async def test_one_failed_shop_does_not_undo_the_linked_shop() -> None:
    service, listings, _batches, _catalog = _service(fail_shop=32)
    accepted = await service.accept_publish(_publish(["21"], ["31", "32"]), tenant_id=9, actor_id=3)
    finished = await service.run_publish(accepted.id)
    assert finished.status == "PARTIAL"
    assert finished.succeeded == 1
    assert finished.failed == 1
    by_shop = {row.shop_id: row for row in listings}
    assert by_shop[31].status == "LINKED"
    assert by_shop[32].status == "DRAFT"
    assert by_shop[32].platform_product_id is None
    failed = next(item for item in finished.items if item.shop_id == 32)
    assert failed.error_message == "平台拒绝刊登"


async def test_price_above_twenty_percent_needs_confirm_and_draft_stays_local() -> None:
    service, listings, batches, catalog = _service()
    draft = _seed(
        listings,
        listing_id=41,
        sku_id=21,
        shop_id=31,
        price=Decimal("10.000000"),
        currency="SGD",
        status="DRAFT",
    )
    payload = {"listing_ids": ["41"], "price": "13.000000", "currency": "SGD"}
    preview = await service.preview_prices(PriceBatchCreate.model_validate({**payload, "confirmed": False}))
    assert preview.needs_confirm is True
    with pytest.raises(AppError) as exc:
        await service.accept_prices(
            PriceBatchCreate.model_validate({**payload, "confirmed": False}),
            tenant_id=9,
            actor_id=3,
        )
    assert exc.value.code == ErrorCode.LISTING_PRICE_CONFIRM_REQUIRED
    assert draft.price == Decimal("10.000000")
    assert batches == []
    accepted = await service.accept_prices(
        PriceBatchCreate.model_validate({**payload, "confirmed": True}),
        tenant_id=9,
        actor_id=3,
    )
    finished = await service.run_prices(accepted.id)
    assert finished.status == "SUCCEEDED"
    assert draft.price == Decimal("13.000000")
    assert isinstance(draft.price, Decimal)
    assert catalog.prices == []


async def test_linked_price_within_twenty_percent_calls_the_platform() -> None:
    service, listings, _batches, catalog = _service()
    linked = _seed(
        listings,
        listing_id=42,
        sku_id=21,
        shop_id=31,
        price=Decimal("10"),
        currency="SGD",
        status="LINKED",
        platform_sku_id="SKU-1",
    )
    exact = PriceBatchCreate.model_validate(
        {"listing_ids": ["42"], "price": "12.000000", "currency": "SGD", "confirmed": False}
    )
    preview = await service.preview_prices(exact)
    assert preview.needs_confirm is False
    accepted = await service.accept_prices(exact, tenant_id=9, actor_id=3)
    finished = await service.run_prices(accepted.id)
    assert finished.status == "SUCCEEDED"
    assert linked.price == Decimal("12.000000")
    assert catalog.prices == [(31, Decimal("12.000000"))]


async def test_missing_listing_is_not_found() -> None:
    service, _listings, _batches, _catalog = _service()
    with pytest.raises(NotFoundError):
        await service.preview_prices(
            PriceBatchCreate.model_validate(
                {"listing_ids": ["99"], "price": "1.000000", "currency": "SGD", "confirmed": True}
            )
        )


@pytest.mark.parametrize(
    "adapter_cls",
    [AmazonAdapter, ShopeeAdapter, LazadaAdapter, TikTokAdapter],
)
async def test_fixture_publish_returns_both_platform_ids(adapter_cls: type) -> None:
    adapter = adapter_cls(transport=FixtureTransport())
    cred = CredentialView(
        shop_id="31",
        platform=adapter.platform,
        site_code="SG",
        access_token="fixture-access-token",
        refresh_token=None,
        expires_at=datetime.now(UTC),
    )
    result = await adapter.publish_product(
        cred,
        UnifiedProduct(
            platform=adapter.platform,
            platform_product_id="",
            title="T恤",
            raw={"sku_code": "TEE-RED", "price": "9.900000", "currency": "SGD"},
        ),
    )
    assert result.platform_product_id.startswith("fixture-")
    assert result.platform_sku_id.startswith("fixture-sku-")
    updated = await adapter.update_price(
        cred,
        [PriceUpdate(platform_sku_id=result.platform_sku_id, price=Decimal("9.900000"), currency="SGD")],
    )
    assert updated.succeeded == 1
    assert updated.failed == 0


async def test_live_publish_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.adapters.catalog.use_fixture_transport", lambda: False)
    adapter = ShopeeAdapter(transport=FixtureTransport())
    cred = CredentialView(
        shop_id="31",
        platform="shopee",
        site_code="SG",
        access_token="token",
        refresh_token=None,
        expires_at=datetime.now(UTC),
    )
    with pytest.raises(AdapterError) as exc:
        await adapter.publish_product(
            cred,
            UnifiedProduct(platform="shopee", platform_product_id="", title="T", raw={}),
        )
    assert exc.value.decision is RetryDecision.FAIL_FAST
