"""图片校验、表格错误行和 Listing 差异确认。"""

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from app.adapters.base import CredentialView, RemoteListing
from app.adapters.catalog import fetch_remote_listing
from app.adapters.errors import AdapterError
from app.adapters.images import ISSUE_NOT_WHITE, ISSUE_TOO_LARGE, ISSUE_TOO_SMALL, assess_image
from app.adapters.shopee.adapter import ShopeeAdapter
from app.adapters.transport import FixtureTransport
from app.core.errors import AppError, ErrorCode
from app.db.base import SoftDeleteMixin, TenantMixin
from app.models import TENANT_SCOPED_TABLES
from app.models.listing import Listing, ListingDiff
from app.models.platform import Shop
from app.models.product import ProductImage, Sku, Spu
from app.object_store import MemoryObjectStore
from app.services.image_file import inspect_image
from app.services.listing_diff import ListingDiffService
from app.services.order_xlsx import build_xlsx
from app.services.product_image import ProductImageService
from app.services.product_sheet import ProductSheetService
from app.services.spreadsheet import read_tabular
from app.sync_engine.queues import task_queue
from app.tasks.sync import patrol_listing_diffs


def _png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (width, height), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _issues(image_type: str, body: bytes, *, white: bool | None = None) -> dict[str, list[str]]:
    inspected = inspect_image(body)
    checks = assess_image(
        image_type=image_type,
        width_px=inspected.width_px,
        height_px=inspected.height_px,
        byte_size=len(body),
        content_type=inspected.content_type,
        white_background=inspected.white_background if white is None else white,
    )
    return {str(item["platform_code"]): list(item["issues"]) for item in checks}  # type: ignore[arg-type]


def test_image_rules_flag_size_and_white_background_without_blocking_save() -> None:
    white = _png(1200, 1200, (255, 255, 255))
    small = _png(100, 100, (255, 255, 255))
    red = _png(1200, 1200, (255, 0, 0))
    wide = _png(3000, 3000, (255, 255, 255))
    assert _issues("MAIN", white)["amazon"] == []
    assert ISSUE_TOO_SMALL in _issues("MAIN", small)["amazon"]
    assert ISSUE_NOT_WHITE in _issues("MAIN", red)["shopee"]
    assert ISSUE_NOT_WHITE not in _issues("GALLERY", red)["shopee"]
    assert ISSUE_TOO_LARGE in _issues("MAIN", wide)["shopee"]
    assert ISSUE_TOO_LARGE not in _issues("MAIN", wide)["amazon"]
    assert inspect_image(white).white_background is True
    assert inspect_image(red).white_background is False


def test_catalog_tables_and_patrol_task_keep_tenant_boundary() -> None:
    assert issubclass(ProductImage, TenantMixin)
    assert issubclass(ProductImage, SoftDeleteMixin)
    assert issubclass(ListingDiff, TenantMixin)
    assert not issubclass(ListingDiff, SoftDeleteMixin)
    assert "product_image" in TENANT_SCOPED_TABLES
    assert "listing_diff" in TENANT_SCOPED_TABLES
    image_index = next(
        item for item in ProductImage.__table__.indexes if item.name == "ix_product_image_tenant_id_spu_id"
    )
    diff_index = next(item for item in ListingDiff.__table__.indexes if item.name == "ix_listing_diff_tenant_id_status")
    assert next(column.name for column in image_index.columns) == "tenant_id"
    assert next(column.name for column in diff_index.columns) == "tenant_id"
    assert "tenant_id" in inspect.signature(patrol_listing_diffs).parameters
    assert task_queue("sync.patrol_listing_diffs") == "sync"
    root = Path(__file__).resolve().parents[2] / "app" / "services"
    for name in ("product_image.py", "product_sheet.py", "listing_diff.py"):
        assert "platform ==" not in (root / name).read_text(encoding="utf-8")


async def test_upload_keeps_non_compliant_image_and_records_the_prompt() -> None:
    session = MagicMock()
    service = ProductImageService(session, store=MemoryObjectStore())
    service.spus = MagicMock()
    service.spus.get_or_404 = AsyncMock(return_value=object())
    saved: list[ProductImage] = []

    async def add(row: ProductImage) -> ProductImage:
        row.id = 7
        saved.append(row)
        return row

    service.images = MagicMock()
    service.images.add = AsyncMock(side_effect=add)
    view = await service.upload(
        _png(100, 100, (255, 255, 255)),
        spu_id=11,
        sku_id=None,
        image_type="main",
        sort=1,
        tenant_id=9,
        actor_id=3,
    )
    assert saved[0].image_type == "MAIN"
    assert view.compliance[0].platform_code == "amazon"
    assert view.compliance[0].ok is False
    assert saved[0].object_key.startswith("9/")


def test_import_reports_the_bad_row_and_writes_nothing() -> None:
    payload = build_xlsx(
        ["SKU编码", "商品标题", "重量克", "长厘米", "宽厘米", "高厘米"],
        [
            ["TEE-1", "T恤", "100", "10", "8", "2"],
            ["TEE-2", "帽子", "0", "10", "8", "2"],
        ],
        sheet_name="products",
    )
    service, created = _sheet_service()
    with pytest.raises(AppError) as caught:
        _run(service.import_file(payload, filename="goods.xlsx", tenant_id=9, actor_id=3))
    assert caught.value.code == ErrorCode.PRODUCT_IMPORT_INVALID
    rows = caught.value.data["rows"]
    assert rows == [{"row": 3, "column": "weight_g", "message": "必须是大于 0 的十进制数字"}]
    assert created == []


def test_import_rejects_more_than_500_rows() -> None:
    payload = build_xlsx(
        ["SKU编码", "商品标题", "重量克", "长厘米", "宽厘米", "高厘米"],
        [[f"SKU-{index}", "商品", "1", "1", "1", "1"] for index in range(501)],
        sheet_name="products",
    )
    service, created = _sheet_service()
    with pytest.raises(AppError) as caught:
        _run(service.import_file(payload, filename="goods.xlsx", tenant_id=9, actor_id=3))
    assert "500" in caught.value.data["rows"][0]["message"]
    assert created == []


def test_import_creates_decimal_measures_and_export_hides_cost() -> None:
    payload = build_xlsx(
        ["SKU编码", "商品标题", "品牌", "重量克", "长厘米", "宽厘米", "高厘米"],
        [["TEE-1", "T恤", "Acme", "100.5", "10", "8", "2"]],
        sheet_name="products",
    )
    service, created = _sheet_service()
    result = _run(service.import_file(payload, filename="goods.xlsx", tenant_id=9, actor_id=3))
    assert result.created == 1
    sku = created[0].skus[0]
    assert sku.weight_g == Decimal("100.5")
    assert sku.purchase_price is None
    sku_row = Sku(
        id=4,
        tenant_id=9,
        spu_id=8,
        sku_code="TEE-1",
        weight_g=Decimal("100.500000"),
        length_cm=Decimal("10"),
        width_cm=Decimal("8"),
        height_cm=Decimal("2"),
        purchase_price=Decimal("3.000000"),
        currency="CNY",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    spu = Spu(
        id=8, tenant_id=9, title="T恤", status="DRAFT", created_at=datetime.now(UTC), updated_at=datetime.now(UTC)
    )
    service.skus.list_for_export = AsyncMock(return_value=[(sku_row, spu)])
    service.audit.append_action = AsyncMock()
    exported = _run(service.export_file(tenant_id=9, actor_id=3, status=None, title=None))
    table = read_tabular(__import__("base64").b64decode(exported.content_base64), exported.filename)
    assert "采购价" not in table[0][1]
    assert table[1][1][4] == "100.500000"


async def test_price_diff_needs_confirm_then_stores_decimal() -> None:
    listing = _linked(Decimal("10.000000"), "SGD")
    service, diffs = _diff_service(listing, RemoteListing(price=Decimal("15.000000"), currency="USD"))
    first = await service.patrol(actor_id=3)
    assert first.created == 2
    assert {row.field_name for row in diffs} == {"price", "currency"}
    again = await service.patrol(actor_id=3)
    assert again.created == 0
    price_row = next(row for row in diffs if row.field_name == "price")
    accepted = await service.accept(price_row.id, actor_id=3)
    assert listing.price == Decimal("15.000000")
    assert isinstance(listing.price, Decimal)
    assert accepted.status == "ACCEPTED"
    currency_row = next(row for row in diffs if row.field_name == "currency")
    await service.dismiss(currency_row.id, actor_id=3)
    assert listing.currency == "SGD"
    third = await service.patrol(actor_id=3)
    assert third.created == 0


async def test_fixture_listing_snapshot_is_decimal_and_live_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    cred = CredentialView(
        shop_id="31",
        platform="shopee",
        site_code="SG",
        access_token="fixture-access-token",
        refresh_token=None,
        expires_at=datetime.now(UTC),
    )
    remote = await fetch_remote_listing(
        FixtureTransport(),
        cred,
        url="https://partner.shopeemobile.com/api/v2/product/listing_snapshot",
        platform_product_id="P1",
        platform_sku_id="S1",
    )
    assert remote.price == Decimal("15.000000")
    assert isinstance(remote.price, Decimal)
    assert remote.currency == "USD"
    monkeypatch.setattr("app.adapters.catalog.use_fixture_transport", lambda: False)
    with pytest.raises(AdapterError):
        await ShopeeAdapter(transport=FixtureTransport()).fetch_listing(
            cred,
            platform_product_id="P1",
            platform_sku_id="S1",
        )


def _sheet_service() -> tuple[ProductSheetService, list]:
    session = MagicMock()
    service = ProductSheetService(session)
    created: list = []

    async def create_spu(draft, **_kwargs):
        created.append(draft)
        return draft

    service.products = MagicMock()
    service.products.create_spu = AsyncMock(side_effect=create_spu)
    service.skus = MagicMock()
    service.skus.code_taken = AsyncMock(return_value=False)
    service.audit = MagicMock()
    return service, created


def _linked(price: Decimal, currency: str) -> Listing:
    now = datetime.now(UTC)
    return Listing(
        id=50,
        tenant_id=9,
        sku_id=21,
        shop_id=31,
        platform_product_id="P-1",
        platform_sku_id="S-1",
        price=price,
        currency=currency,
        attr_values={},
        status="LINKED",
        created_at=now,
        updated_at=now,
    )


def _diff_service(listing: Listing, remote: RemoteListing) -> tuple[ListingDiffService, list[ListingDiff]]:
    session = MagicMock()
    session.flush = AsyncMock()

    class _Snapshots:
        async def fetch(self, shop: Shop, *, platform_product_id: str, platform_sku_id: str) -> RemoteListing:
            del shop, platform_product_id, platform_sku_id
            return remote

    service = ListingDiffService(session, snapshots=_Snapshots())
    diffs: list[ListingDiff] = []

    async def add(row: ListingDiff) -> ListingDiff:
        row.id = 80 + len(diffs)
        row.created_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        diffs.append(row)
        return row

    service.listings = MagicMock()
    service.diffs = MagicMock()
    service.shops = MagicMock()
    service.skus = MagicMock()
    service.listings.list_linked = AsyncMock(return_value=[listing])
    service.listings.get_or_404 = AsyncMock(return_value=listing)
    service.listings.get_many = AsyncMock(return_value=[listing])
    service.shops.get = AsyncMock(
        return_value=Shop(
            id=31,
            tenant_id=9,
            platform_code="shopee",
            site_code="SG",
            shop_name="新加坡店",
            platform_shop_id="seller-31",
            status=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    service.skus.get = AsyncMock(
        return_value=Sku(
            id=21,
            tenant_id=9,
            spu_id=11,
            sku_code="TEE-1",
            weight_g=Decimal("10"),
            length_cm=Decimal("2"),
            width_cm=Decimal("3"),
            height_cm=Decimal("4"),
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    service.diffs.add = AsyncMock(side_effect=add)
    service.diffs.get_or_404 = AsyncMock(
        side_effect=lambda entity_id: next(row for row in diffs if row.id == entity_id)
    )
    service.diffs.pending = AsyncMock(
        side_effect=lambda listing_id, field: next(
            (
                row
                for row in diffs
                if row.listing_id == listing_id and row.field_name == field and row.status == "PENDING"
            ),
            None,
        )
    )
    service.diffs.latest_closed = AsyncMock(
        side_effect=lambda listing_id, field: next(
            (
                row
                for row in reversed(diffs)
                if row.listing_id == listing_id and row.field_name == field and row.status != "PENDING"
            ),
            None,
        )
    )
    return service, diffs


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
