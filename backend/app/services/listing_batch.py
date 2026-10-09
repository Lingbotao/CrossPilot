"""批量刊登与批量改价。

受理只落批次。真正调平台的是 ``run_publish`` / ``run_prices``，由 Celery 传入 tenant_id 后调用。
售价不是采购价，不按成本权限剥离。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import CredentialView, PriceUpdate, PublishResult, UnifiedProduct
from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError, RetryDecision
from app.adapters.locale import content_language
from app.adapters.registry import adapter_registry
from app.adapters.transport import use_fixture_transport
from app.core.context import get_tenant_id
from app.core.errors import AppError, ErrorCode, NotFoundError, ParamInvalidError
from app.models.listing import MAX_BATCH_ITEMS, Listing, ListingBatch, ListingBatchItem
from app.models.platform import Shop
from app.models.product import Sku
from app.repositories.listing import ListingBatchItemRepository, ListingBatchRepository, ListingRepository
from app.repositories.locale import ListingContentRepository
from app.repositories.platform import ShopCredentialRepository, ShopRepository
from app.repositories.product import SkuRepository, SpuRepository
from app.schemas.listing_batch import (
    ListingBatchItemView,
    ListingBatchView,
    PriceBatchCreate,
    PriceBatchPreview,
    PricePreview,
    PricePreviewLine,
    PublishBatchCreate,
)
from app.services.credential_service import view_from_row
from app.services.locale_text import require_published_title

PRICE_CHANGE_CONFIRM_RATIO = Decimal("0.20")
BatchWorker = Callable[[ListingBatch, ListingBatchItem], Awaitable[None]]


class CatalogPort(Protocol):
    async def publish(
        self, shop: Shop, *, title: str, sku_code: str, price: Decimal, currency: str
    ) -> PublishResult: ...

    async def update_price(self, shop: Shop, *, platform_sku_id: str, price: Decimal, currency: str) -> None: ...


def unique_ids(values: list[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def expand_pairs(sku_ids: list[int], shop_ids: list[int]) -> list[tuple[int, int]]:
    pairs = [(sku_id, shop_id) for sku_id in unique_ids(sku_ids) for shop_id in unique_ids(shop_ids)]
    if len(pairs) > MAX_BATCH_ITEMS:
        raise AppError(
            "单批最多 500 条",
            code=ErrorCode.LISTING_BATCH_TOO_LARGE,
            data={"limit": MAX_BATCH_ITEMS, "count": len(pairs)},
        )
    return pairs


def needs_price_confirm(old: Decimal | None, new: Decimal) -> bool:
    """原价为空或 0，或者涨跌幅度严格大于 20%，都要再次确认。"""

    if old is None or old == 0:
        return True
    return abs(new - old) / old > PRICE_CHANGE_CONFIRM_RATIO


def apply_counts(batch: ListingBatch, items: list[ListingBatchItem], *, running: bool) -> None:
    batch.succeeded = sum(1 for item in items if item.status == "SUCCEEDED")
    batch.failed = sum(1 for item in items if item.status == "FAILED")
    batch.skipped = sum(1 for item in items if item.status == "SKIPPED")
    if any(item.status == "PENDING" for item in items):
        batch.status = "RUNNING" if running else "PENDING"
        return
    if batch.failed and (batch.succeeded or batch.skipped):
        batch.status = "PARTIAL"
        return
    batch.status = "FAILED" if batch.failed else "SUCCEEDED"


def _error_text(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text[:512]


class AdapterCatalog:
    """通过注册表调用平台。业务服务不按平台分支。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def publish(self, shop: Shop, *, title: str, sku_code: str, price: Decimal, currency: str) -> PublishResult:
        register_builtin_adapters()
        cred = await self._credential(shop)
        product = UnifiedProduct(
            platform=shop.platform_code,
            platform_product_id="",
            title=title,
            raw={"sku_code": sku_code, "price": format(price, "f"), "currency": currency},
        )
        return await adapter_registry.get(shop.platform_code).publish_product(cred, product)

    async def update_price(self, shop: Shop, *, platform_sku_id: str, price: Decimal, currency: str) -> None:
        register_builtin_adapters()
        cred = await self._credential(shop)
        result = await adapter_registry.get(shop.platform_code).update_price(
            cred,
            [PriceUpdate(platform_sku_id=platform_sku_id, price=price, currency=currency)],
        )
        if result.failed:
            raise AdapterError("平台拒绝改价", platform=shop.platform_code, decision=RetryDecision.FAIL_FAST)

    async def _credential(self, shop: Shop) -> CredentialView:
        row = await ShopCredentialRepository(self.session).get_by_shop_id(shop.id)
        if row is not None:
            return view_from_row(shop, row)
        if use_fixture_transport():
            return CredentialView(
                shop_id=str(shop.id),
                platform=shop.platform_code,
                site_code=shop.site_code,
                access_token="fixture-access-token",  # noqa: S106 - fixture 传输用的占位令牌，不是密钥
                refresh_token=None,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        raise AppError("店铺尚未授权", code=ErrorCode.GRANT_FAILED)


class ListingBatchService:
    def __init__(self, session: AsyncSession, *, catalog: CatalogPort | None = None) -> None:
        self.session = session
        self.listings = ListingRepository(session)
        self.batches = ListingBatchRepository(session)
        self.items = ListingBatchItemRepository(session)
        self.skus = SkuRepository(session)
        self.shops = ShopRepository(session)
        self.spus = SpuRepository(session)
        self.contents = ListingContentRepository(session)
        self.catalog: CatalogPort = catalog if catalog is not None else AdapterCatalog(session)

    async def _bind_tenant(self) -> None:
        tenant_id = get_tenant_id()
        if tenant_id is None:
            return
        from app.db.session import apply_rls_tenant

        await apply_rls_tenant(self.session, tenant_id)

    async def _commit(self) -> None:
        await self.session.commit()
        await self._bind_tenant()

    async def _rollback(self) -> None:
        await self.session.rollback()
        await self._bind_tenant()

    async def accept_publish(self, payload: PublishBatchCreate, *, tenant_id: int, actor_id: int) -> ListingBatchView:
        pairs = expand_pairs(payload.sku_ids, payload.shop_ids)
        sku_map = {sku_id: await self.skus.get_or_404(sku_id) for sku_id in unique_ids(payload.sku_ids)}
        shop_map = {shop_id: await self.shops.get_or_404(shop_id) for shop_id in unique_ids(payload.shop_ids)}
        batch = ListingBatch(
            tenant_id=tenant_id,
            kind="PUBLISH",
            status="PENDING",
            total=len(pairs),
            succeeded=0,
            failed=0,
            skipped=0,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.batches.add(batch)
        rows: list[ListingBatchItem] = []
        for sku_id, shop_id in pairs:
            rows.append(
                await self._stage_publish(batch, sku_map[sku_id], shop_map[shop_id], payload, actor_id=actor_id)
            )
        apply_counts(batch, rows, running=False)
        await self._commit()
        return _view(batch, rows)

    async def run_publish(self, batch_id: int) -> ListingBatchView:
        return await self._run(batch_id, kind="PUBLISH", worker=self._publish_item)

    async def preview_prices(self, payload: PriceBatchPreview) -> PricePreview:
        rows = await self._load_listings(payload.listing_ids)
        lines = [_preview_line(row, payload.price, payload.currency) for row in rows]
        return PricePreview(needs_confirm=any(line.needs_confirm for line in lines), lines=lines)

    async def accept_prices(self, payload: PriceBatchCreate, *, tenant_id: int, actor_id: int) -> ListingBatchView:
        listings = await self._load_listings(payload.listing_ids)
        if any(needs_price_confirm(row.price, payload.price) for row in listings) and not payload.confirmed:
            raise AppError(
                "改价幅度超过 20%，需要再次确认",
                code=ErrorCode.LISTING_PRICE_CONFIRM_REQUIRED,
                data={"ratio": str(PRICE_CHANGE_CONFIRM_RATIO)},
            )
        batch = ListingBatch(
            tenant_id=tenant_id,
            kind="PRICE",
            status="PENDING",
            total=len(listings),
            succeeded=0,
            failed=0,
            skipped=0,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.batches.add(batch)
        rows: list[ListingBatchItem] = []
        for listing in listings:
            rows.append(await self.items.add(_price_item(batch, listing, payload, actor_id)))
        apply_counts(batch, rows, running=False)
        await self._commit()
        return _view(batch, rows)

    async def run_prices(self, batch_id: int) -> ListingBatchView:
        return await self._run(batch_id, kind="PRICE", worker=self._reprice_item)

    async def get_batch(self, batch_id: int) -> ListingBatchView:
        batch = await self.batches.get_or_404(batch_id)
        return _view(batch, await self.items.list_for_batch(batch.id))

    async def _run(self, batch_id: int, *, kind: str, worker: BatchWorker) -> ListingBatchView:
        batch = await self.batches.get_or_404(batch_id)
        if batch.kind != kind:
            raise ParamInvalidError("批次类型不正确")
        items = await self.items.list_for_batch(batch.id)
        if batch.status in {"SUCCEEDED", "PARTIAL", "FAILED"}:
            return _view(batch, items)
        while True:
            batch = await self.batches.get_or_404(batch_id)
            items = await self.items.list_for_batch(batch.id)
            pending = next((item for item in items if item.status == "PENDING"), None)
            if pending is None:
                apply_counts(batch, items, running=False)
                await self._commit()
                return _view(batch, items)
            batch.status = "RUNNING"
            try:
                await worker(batch, pending)
                apply_counts(batch, items, running=True)
                await self._commit()
            except Exception as exc:
                await self._rollback()
                await self._mark_failed(batch_id, pending.id, exc)

    async def _mark_failed(self, batch_id: int, item_id: int, exc: Exception) -> None:
        batch = await self.batches.get_or_404(batch_id)
        items = await self.items.list_for_batch(batch_id)
        for item in items:
            if item.id == item_id:
                item.status = "FAILED"
                item.error_message = _error_text(exc)
        apply_counts(batch, items, running=True)
        await self._commit()

    async def _stage_publish(
        self,
        batch: ListingBatch,
        sku: Sku,
        shop: Shop,
        payload: PublishBatchCreate,
        *,
        actor_id: int,
    ) -> ListingBatchItem:
        linked = await self.listings.linked_pair(sku.id, shop.id)
        if linked is not None:
            return await self.items.add(
                _item(
                    batch,
                    sku_id=sku.id,
                    shop_id=shop.id,
                    listing_id=linked.id,
                    status="SKIPPED",
                    price_before=linked.price,
                    currency_before=linked.currency,
                    price_after=None,
                    currency_after=None,
                    actor_id=actor_id,
                )
            )
        current = await self.listings.open_pair(sku.id, shop.id)
        before_price = current.price if current is not None else None
        before_currency = current.currency if current is not None else None
        if current is None:
            current = Listing(
                tenant_id=batch.tenant_id,
                sku_id=sku.id,
                shop_id=shop.id,
                price=payload.price,
                currency=payload.currency,
                attr_values={},
                status="DRAFT",
                created_by=actor_id,
                updated_by=actor_id,
            )
            await self.listings.add(current)
        else:
            current.price = payload.price
            current.currency = payload.currency
            current.updated_by = actor_id
        return await self.items.add(
            _item(
                batch,
                sku_id=sku.id,
                shop_id=shop.id,
                listing_id=current.id,
                status="PENDING",
                price_before=before_price,
                currency_before=before_currency,
                price_after=payload.price,
                currency_after=payload.currency,
                actor_id=actor_id,
            )
        )

    async def _publish_item(self, batch: ListingBatch, item: ListingBatchItem) -> None:
        if item.listing_id is None:
            raise ParamInvalidError("缺少 Listing")
        listing = await self.listings.get_or_404(item.listing_id)
        sku = await self.skus.get_or_404(item.sku_id)
        shop = await self.shops.get_or_404(item.shop_id)
        if listing.price is None or listing.currency is None:
            raise ParamInvalidError("刊登需要售价和币种")
        await self.spus.get_or_404(sku.spu_id)
        lang = content_language(shop.site_code)
        if lang is None:
            raise AppError(
                "该站点没有内容语言",
                code=ErrorCode.CONTENT_NOT_REVIEWED,
                data={"site_code": shop.site_code},
            )
        content = await self.contents.get_active(listing.id, lang)
        title = require_published_title(
            "" if content is None else content.quality_status,
            "" if content is None else content.title,
            lang=lang,
        )
        result = await self.catalog.publish(
            shop,
            title=title,
            sku_code=sku.sku_code,
            price=listing.price,
            currency=listing.currency,
        )
        listing.platform_product_id = result.platform_product_id
        listing.platform_sku_id = result.platform_sku_id
        listing.status = "LINKED"
        listing.updated_by = batch.created_by
        await self.listings.flush_unique(result.platform_sku_id)
        item.status = "SUCCEEDED"
        item.error_message = None

    async def _reprice_item(self, batch: ListingBatch, item: ListingBatchItem) -> None:
        if item.listing_id is None or item.price_after is None or item.currency_after is None:
            raise ParamInvalidError("售价和币种需要同时填写")
        listing = await self.listings.get_or_404(item.listing_id)
        if listing.status == "LINKED" and listing.platform_sku_id:
            shop = await self.shops.get_or_404(item.shop_id)
            await self.catalog.update_price(
                shop,
                platform_sku_id=listing.platform_sku_id,
                price=item.price_after,
                currency=item.currency_after,
            )
        listing.price = item.price_after
        listing.currency = item.currency_after
        listing.updated_by = batch.created_by
        item.status = "SUCCEEDED"
        item.error_message = None

    async def _load_listings(self, listing_ids: list[int]) -> list[Listing]:
        ids = unique_ids(listing_ids)
        if len(ids) > MAX_BATCH_ITEMS:
            raise AppError(
                "单批最多 500 条",
                code=ErrorCode.LISTING_BATCH_TOO_LARGE,
                data={"limit": MAX_BATCH_ITEMS, "count": len(ids)},
            )
        found = await self.listings.get_many(ids)
        by_id = {row.id: row for row in found}
        if len(by_id) != len(ids):
            raise NotFoundError()
        return [by_id[entity_id] for entity_id in ids]


def _item(
    batch: ListingBatch,
    *,
    sku_id: int,
    shop_id: int,
    listing_id: int | None,
    status: str,
    price_before: Decimal | None,
    currency_before: str | None,
    price_after: Decimal | None,
    currency_after: str | None,
    actor_id: int,
) -> ListingBatchItem:
    return ListingBatchItem(
        tenant_id=batch.tenant_id,
        batch_id=batch.id,
        sku_id=sku_id,
        shop_id=shop_id,
        listing_id=listing_id,
        status=status,
        price_before=price_before,
        currency_before=currency_before,
        price_after=price_after,
        currency_after=currency_after,
        created_by=actor_id,
        updated_by=actor_id,
    )


def _price_item(batch: ListingBatch, listing: Listing, payload: PriceBatchCreate, actor_id: int) -> ListingBatchItem:
    return _item(
        batch,
        sku_id=listing.sku_id,
        shop_id=listing.shop_id,
        listing_id=listing.id,
        status="PENDING",
        price_before=listing.price,
        currency_before=listing.currency,
        price_after=payload.price,
        currency_after=payload.currency,
        actor_id=actor_id,
    )


def _preview_line(row: Listing, price: Decimal, currency: str) -> PricePreviewLine:
    return PricePreviewLine(
        listing_id=row.id,
        sku_id=row.sku_id,
        shop_id=row.shop_id,
        price_before=row.price,
        currency_before=row.currency,
        price_after=price,
        currency_after=currency,
        needs_confirm=needs_price_confirm(row.price, price),
    )


def _view(batch: ListingBatch, items: list[ListingBatchItem]) -> ListingBatchView:
    return ListingBatchView(
        id=batch.id,
        kind=batch.kind,
        status=batch.status,
        total=batch.total,
        succeeded=batch.succeeded,
        failed=batch.failed,
        skipped=batch.skipped,
        items=[ListingBatchItemView.model_validate(item) for item in items],
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )
