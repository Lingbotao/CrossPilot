"""Listing 反向同步。只对比本地已经保存的售价和币种，确认后才写回。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import CredentialView, RemoteListing
from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError
from app.adapters.registry import adapter_registry
from app.adapters.transport import use_fixture_transport
from app.core.errors import AppError, ErrorCode, NotFoundError
from app.core.pagination import PageData, build_cursor_page, decode_cursor
from app.models.listing import Listing, ListingDiff
from app.models.platform import Shop
from app.repositories.listing import ListingDiffRepository, ListingRepository
from app.repositories.platform import ShopCredentialRepository, ShopRepository
from app.repositories.product import SkuRepository
from app.schemas.listing_diff import ListingDiffView, ListingPatrolResult
from app.services.credential_service import view_from_row

PATROL_LIMIT = 500
_FIELDS = ("price", "currency")


class SnapshotPort(Protocol):
    async def fetch(self, shop: Shop, *, platform_product_id: str, platform_sku_id: str) -> RemoteListing: ...


class AdapterSnapshot:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def fetch(self, shop: Shop, *, platform_product_id: str, platform_sku_id: str) -> RemoteListing:
        register_builtin_adapters()
        cred = await self._credential(shop)
        return await adapter_registry.get(shop.platform_code).fetch_listing(
            cred,
            platform_product_id=platform_product_id,
            platform_sku_id=platform_sku_id,
        )

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


class ListingDiffService:
    def __init__(self, session: AsyncSession, *, snapshots: SnapshotPort | None = None) -> None:
        self.session = session
        self.listings = ListingRepository(session)
        self.diffs = ListingDiffRepository(session)
        self.shops = ShopRepository(session)
        self.skus = SkuRepository(session)
        self.snapshots: SnapshotPort = snapshots if snapshots is not None else AdapterSnapshot(session)

    async def list_diffs(
        self,
        *,
        limit: int,
        cursor: str | None,
        status: str | None,
    ) -> PageData[ListingDiffView]:
        before_id = _before(cursor)
        rows = await self.diffs.list_cursor(limit=limit, before_id=before_id, status=status)
        page = build_cursor_page(rows, limit)
        return PageData[ListingDiffView](items=await self._views(page.items), page_info=page.page_info)

    async def patrol(self, *, actor_id: int | None) -> ListingPatrolResult:
        listings = await self.listings.list_linked(limit=PATROL_LIMIT)
        created = 0
        for listing in listings:
            created += await self._compare(listing, actor_id)
        await self.session.flush()
        return ListingPatrolResult(created=created, scanned=len(listings))

    async def accept(self, diff_id: int, *, actor_id: int) -> ListingDiffView:
        diff, listing = await self._pending(diff_id)
        if diff.field_name == "price":
            listing.price = _accept_price(diff.remote_value, listing.currency)
        elif diff.field_name == "currency":
            listing.currency = _accept_currency(diff.remote_value, listing.price)
        else:
            raise AppError("不支持的差异字段", code=ErrorCode.LISTING_STATE_INVALID)
        diff.status = "ACCEPTED"
        diff.updated_by = actor_id
        listing.updated_by = actor_id
        await self.session.flush()
        return (await self._views([diff]))[0]

    async def dismiss(self, diff_id: int, *, actor_id: int) -> ListingDiffView:
        diff, _listing = await self._pending(diff_id)
        diff.status = "DISMISSED"
        diff.updated_by = actor_id
        await self.session.flush()
        return (await self._views([diff]))[0]

    async def _compare(self, listing: Listing, actor_id: int | None) -> int:
        product_id = listing.platform_product_id
        sku_id = listing.platform_sku_id
        if not product_id or not sku_id:
            return 0
        shop = await self.shops.get(listing.shop_id)
        if shop is None:
            return 0
        try:
            remote = await self.snapshots.fetch(shop, platform_product_id=product_id, platform_sku_id=sku_id)
        except (AdapterError, AppError):
            return 0
        local = {"price": _money(listing.price), "currency": listing.currency}
        remote_values = {"price": format(remote.price, "f"), "currency": remote.currency.upper()}
        created = 0
        for field in _FIELDS:
            if _differs(field, local[field], remote_values[field]):
                created += await self._open(listing, field, local[field], remote_values[field], actor_id)
        return created

    async def _open(
        self,
        listing: Listing,
        field: str,
        local: str | None,
        remote: str,
        actor_id: int | None,
    ) -> int:
        pending = await self.diffs.pending(listing.id, field)
        if pending is not None:
            pending.local_value = local
            pending.remote_value = remote
            pending.updated_by = actor_id
            return 0
        closed = await self.diffs.latest_closed(listing.id, field)
        if closed is not None and closed.status == "DISMISSED" and closed.remote_value == remote:
            return 0
        await self.diffs.add(
            ListingDiff(
                tenant_id=listing.tenant_id,
                listing_id=listing.id,
                shop_id=listing.shop_id,
                field_name=field,
                local_value=local,
                remote_value=remote,
                status="PENDING",
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        return 1

    async def _pending(self, diff_id: int) -> tuple[ListingDiff, Listing]:
        diff = await self.diffs.get_or_404(diff_id)
        if diff.status != "PENDING":
            raise AppError("这条差异已经处理过", code=ErrorCode.LISTING_STATE_INVALID)
        listing = await self.listings.get_or_404(diff.listing_id)
        return diff, listing

    async def _views(self, rows: list[ListingDiff]) -> list[ListingDiffView]:
        listings = {row.id: row for row in await self.listings.get_many([item.listing_id for item in rows])}
        shops: dict[int, Shop] = {}
        for shop_id in {item.shop_id for item in rows}:
            shop = await self.shops.get(shop_id)
            if shop is not None:
                shops[shop_id] = shop
        sku_codes: dict[int, str] = {}
        for listing in listings.values():
            if listing.sku_id in sku_codes:
                continue
            sku = await self.skus.get(listing.sku_id)
            if sku is not None:
                sku_codes[listing.sku_id] = sku.sku_code
        views: list[ListingDiffView] = []
        for item in rows:
            linked = listings.get(item.listing_id)
            shop = shops.get(item.shop_id)
            code = ""
            if linked is not None:
                code = sku_codes.get(linked.sku_id, "")
            views.append(
                ListingDiffView(
                    id=item.id,
                    listing_id=item.listing_id,
                    shop_id=item.shop_id,
                    shop_name=shop.shop_name if shop is not None else "",
                    sku_code=code,
                    field_name=item.field_name,
                    local_value=item.local_value,
                    remote_value=item.remote_value,
                    status=item.status,
                )
            )
        return views


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _differs(field: str, local: str | None, remote: str) -> bool:
    if field == "price":
        if local is None:
            return True
        return Decimal(local) != Decimal(remote)
    return (local or "").upper() != remote.upper()


def _accept_price(remote: str, currency: str | None) -> Decimal:
    try:
        price = Decimal(remote)
    except InvalidOperation as exc:
        raise AppError("平台售价无法采纳", code=ErrorCode.LISTING_STATE_INVALID) from exc
    if price < 0 or currency is None:
        raise AppError("平台售价无法采纳", code=ErrorCode.LISTING_STATE_INVALID)
    return price


def _accept_currency(remote: str, price: Decimal | None) -> str:
    currency = remote.upper()
    if len(currency) != 3 or price is None:
        raise AppError("平台币种无法采纳", code=ErrorCode.LISTING_STATE_INVALID)
    return currency


def _before(cursor: str | None) -> int | None:
    if not cursor:
        return None
    raw = decode_cursor(cursor).get("id")
    if raw is None:
        raise NotFoundError()
    try:
        return int(raw)
    except ValueError as exc:
        raise NotFoundError() from exc


async def list_linked_tenants() -> list[int]:
    from app.db.session import owner_session_scope

    async with owner_session_scope() as session:
        return await ListingRepository(session).tenant_ids_with_linked()


__all__ = ["PATROL_LIMIT", "ListingDiffService", "list_linked_tenants"]
