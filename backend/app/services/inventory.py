"""多仓台账、预占释放、回传和补货建议。

服务不写 SQL。平台差异只出现在适配器。回传批量大小和滞后阈值来自限流配置。
"""

from __future__ import annotations

import base64
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import BatchResult, CredentialView, InventoryUpdate
from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError, RetryDecision
from app.adapters.registry import SUPPORTED_PLATFORMS, adapter_registry
from app.adapters.transport import use_fixture_transport
from app.core.config import settings
from app.core.context import get_tenant_id
from app.core.errors import (
    AppError,
    ErrorCode,
    InventoryConflictError,
    InventoryInsufficientError,
    NotFoundError,
    ParamInvalidError,
    StockTakingStateError,
    TransferStateError,
    TransferWarehouseError,
)
from app.core.logging import get_logger
from app.core.pagination import PageData, build_cursor_page, decode_cursor
from app.engines.inventory import (
    Allocation,
    BinView,
    StockMove,
    cancel_transit_moves,
    chunk_updates,
    dead_stock_amount,
    ensure_utc,
    mark_posted,
    plan_allocations,
    platform_push_quantity,
    receive_move,
    restock_bucket,
    sellable_qty,
    ship_moves,
    should_push_zero,
    should_reserve_line,
    suggest_replenishment,
    taking_delta,
    taking_fits,
    turnover_days,
)
from app.models.enums import AuditAction
from app.models.inventory import (
    DEFAULT_SAFE_STOCK,
    FLOW_TYPES,
    Inventory,
    InventoryFlow,
    InventoryHold,
    InventoryPushLog,
    PlatformSafetyStock,
    StockTaking,
    StockTakingLine,
    StockTransfer,
    StockTransferLine,
    Warehouse,
)
from app.models.listing import Listing
from app.models.order import OrderItem, ReturnOrder
from app.models.platform import Shop
from app.repositories.identity import AuditLogRepository
from app.repositories.inventory import (
    InventoryFlowRepository,
    InventoryHoldRepository,
    InventoryPushLogRepository,
    InventoryRepository,
    PhysicalRow,
    PlatformSafetyStockRepository,
    StockTakingRepository,
    StockTransferRepository,
    WarehouseRepository,
)
from app.repositories.listing import ListingRepository
from app.repositories.order_read import OrderReadRepository
from app.repositories.platform import ShopCredentialRepository, ShopRepository
from app.repositories.product import SkuRepository
from app.schemas.inventory import (
    FlowView,
    InventoryAdjust,
    InventoryView,
    PushLogView,
    ReplenishmentView,
    SafetyStockView,
    SafetyStockWrite,
    StockTakingCounts,
    StockTakingCreate,
    StockTakingLineView,
    StockTakingView,
    TransferCreate,
    TransferLineView,
    TransferView,
    TurnoverView,
    WarehousePatch,
    WarehouseView,
    WarehouseWrite,
)
from app.schemas.order import FilePayload
from app.services.credential_service import view_from_row
from app.services.order_xlsx import build_xlsx
from app.services.rate_limit_config import RateLimitConfigService
from app.sync_engine.errors import StoreUnavailable
from app.sync_engine.runtime import get_rate_limiter

log = get_logger(__name__)

_VERSION_RETRIES = 5
_SWEEP_LISTING_LIMIT = 500
_OPEN_OR_DONE = frozenset({"OPEN", "RELEASED", "SHIPPED"})


@dataclass(frozen=True, slots=True)
class PushRun:
    logs: list[InventoryPushLog]
    retry: bool = False
    error: str = ""
    decision: RetryDecision = RetryDecision.FAIL_FAST
    retry_after_seconds: float | None = None
    zeroed: int = 0


class StockPort(Protocol):
    async def update(self, shop: Shop, items: list[InventoryUpdate]) -> BatchResult: ...


def _cursor_id(cursor: str | None) -> int | None:
    if not cursor:
        return None
    raw = decode_cursor(cursor).get("id")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text.isascii() or not text.isdigit():
        return None
    return int(text)


def _alloc_maps(items: Sequence[Allocation]) -> list[dict[str, int]]:
    return [{"warehouse_id": item.warehouse_id, "quantity": item.quantity} for item in items]


def _read_allocs(raw: list[dict[str, Any]]) -> list[Allocation]:
    found: list[Allocation] = []
    for item in raw:
        warehouse_id = int(item["warehouse_id"])
        quantity = int(item["quantity"])
        if quantity > 0:
            found.append(Allocation(warehouse_id=warehouse_id, quantity=quantity))
    return found


class AdapterStock:
    """通过注册表回传库存。业务代码不按平台分支。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def update(self, shop: Shop, items: list[InventoryUpdate]) -> BatchResult:
        register_builtin_adapters()
        cred = await self._credential(shop)
        return await adapter_registry.get(shop.platform_code).update_inventory(cred, items)

    async def _credential(self, shop: Shop) -> CredentialView:
        row = await ShopCredentialRepository(self.session).get_by_shop_id(shop.id)
        if row is not None:
            return view_from_row(shop, row)
        if use_fixture_transport():
            return CredentialView(
                shop_id=str(shop.id),
                platform=shop.platform_code,
                site_code=shop.site_code,
                access_token="fixture-access-token",  # noqa: S106 - fixture 占位令牌，不是密钥
                refresh_token=None,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        raise AppError("店铺尚未授权", code=ErrorCode.GRANT_FAILED)


class InventoryService:
    def __init__(self, session: AsyncSession, *, stock: StockPort | None = None) -> None:
        self.session = session
        self.warehouses = WarehouseRepository(session)
        self.inventories = InventoryRepository(session)
        self.flows = InventoryFlowRepository(session)
        self.holds = InventoryHoldRepository(session)
        self.safety = PlatformSafetyStockRepository(session)
        self.push_logs = InventoryPushLogRepository(session)
        self.transfers = StockTransferRepository(session)
        self.takings = StockTakingRepository(session)
        self.skus = SkuRepository(session)
        self.listings = ListingRepository(session)
        self.shops = ShopRepository(session)
        self.orders = OrderReadRepository(session)
        self.audit = AuditLogRepository(session)
        self.stock: StockPort = stock if stock is not None else AdapterStock(session)
        self._touched: set[int] = set()

    async def list_warehouses(self, *, limit: int, cursor: str | None) -> PageData[WarehouseView]:
        rows = await self.warehouses.list_cursor(limit=limit, before_id=_cursor_id(cursor))
        return build_cursor_page([_warehouse_view(row) for row in rows], limit)

    async def create_warehouse(self, payload: WarehouseWrite, *, tenant_id: int, actor_id: int) -> WarehouseView:
        if payload.is_default:
            await self.warehouses.clear_default(except_id=None)
        row = Warehouse(
            tenant_id=tenant_id,
            name=payload.name,
            warehouse_type=payload.warehouse_type,
            country=payload.country,
            address=payload.address,
            external_code=payload.external_code,
            is_default=payload.is_default,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.warehouses.add(row)
        return _warehouse_view(row)

    async def update_warehouse(self, warehouse_id: int, payload: WarehousePatch, *, actor_id: int) -> WarehouseView:
        row = await self.warehouses.get_or_404(warehouse_id)
        if payload.is_default is True:
            await self.warehouses.clear_default(except_id=row.id)
        if payload.name is not None:
            row.name = payload.name
        if payload.warehouse_type is not None:
            row.warehouse_type = payload.warehouse_type
        if payload.country is not None:
            row.country = payload.country
        if payload.address is not None:
            row.address = payload.address
        if payload.external_code is not None or "external_code" in payload.model_fields_set:
            row.external_code = payload.external_code
        if payload.is_default is not None:
            row.is_default = payload.is_default
        row.updated_by = actor_id
        await self.warehouses.save(row)
        return _warehouse_view(row)

    async def delete_warehouse(self, warehouse_id: int, *, actor_id: int) -> WarehouseView:
        row = await self.warehouses.get_or_404(warehouse_id)
        balances = await self.inventories.list_by_warehouse(warehouse_id)
        if any(item.available or item.occupied or item.in_transit or item.defective for item in balances):
            raise ParamInvalidError("仓库里还有库存，不能删除")
        row.updated_by = actor_id
        await self.warehouses.soft_delete(row)
        return _warehouse_view(row)

    async def list_inventories(
        self,
        *,
        limit: int,
        cursor: str | None,
        sku_id: int | None,
        warehouse_id: int | None,
    ) -> PageData[InventoryView]:
        rows = await self.inventories.list_sellable(
            limit=limit,
            before_id=_cursor_id(cursor),
            sku_id=sku_id,
            warehouse_id=warehouse_id,
        )
        return build_cursor_page([_inventory_view(row) for row in rows], limit)

    async def adjust(self, payload: InventoryAdjust, *, tenant_id: int, actor_id: int) -> InventoryView:
        if payload.kind != "ADJUST" and payload.quantity <= 0:
            raise ParamInvalidError("数量必须大于 0")
        if payload.kind == "ADJUST" and payload.quantity == 0:
            raise ParamInvalidError("调整数量不能为 0")
        row = await self._ensure(payload.sku_id, payload.warehouse_id, tenant_id=tenant_id, actor_id=actor_id)
        deltas, flow_type, before_name = _adjust_deltas(payload.kind, payload.quantity)
        fresh = await self._commit_change(
            row,
            flow_type=flow_type,
            quantity=abs(payload.quantity),
            before_name=before_name,
            ref_type=payload.ref_type or "ADJUST",
            ref_id=payload.ref_id,
            actor_id=actor_id,
            **deltas,
        )
        await self._flush_enqueue()
        return await self._view_for(fresh)

    async def post_stock(
        self,
        *,
        sku_id: int,
        warehouse_id: int,
        tenant_id: int,
        actor_id: int,
        available_delta: int = 0,
        in_transit_delta: int = 0,
        ref_type: str,
        ref_id: int | None,
    ) -> None:
        """采购收货和头程搬运用。在途和实物分开记流水。"""

        if available_delta == 0 and in_transit_delta == 0:
            return
        row = await self._ensure(sku_id, warehouse_id, tenant_id=tenant_id, actor_id=actor_id)
        if available_delta:
            row = await self._commit_change(
                row,
                flow_type="INBOUND" if available_delta > 0 else "OUTBOUND",
                quantity=abs(available_delta),
                before_name="available",
                ref_type=ref_type,
                ref_id=ref_id,
                actor_id=actor_id,
                available_delta=available_delta,
            )
        if in_transit_delta:
            await self._commit_change(
                row,
                flow_type="ADJUST",
                quantity=abs(in_transit_delta),
                before_name="in_transit",
                ref_type=ref_type,
                ref_id=ref_id,
                actor_id=actor_id,
                in_transit_delta=in_transit_delta,
            )
        await self._flush_enqueue()

    async def set_safe_stock(self, inventory_id: int, safe_stock: int, *, actor_id: int) -> InventoryView:
        row = await self.inventories.get_or_404(inventory_id)
        row.safe_stock = safe_stock
        row.updated_by = actor_id
        await self.session.flush()
        return await self._view_for(row)

    async def list_flows(
        self,
        *,
        limit: int,
        cursor: str | None,
        sku_id: int | None,
        warehouse_id: int | None,
        ref_type: str | None,
        ref_id: int | None,
        flow_type: str | None,
    ) -> PageData[FlowView]:
        if flow_type is not None and flow_type not in FLOW_TYPES:
            raise ParamInvalidError("流水类型不支持")
        rows = await self.flows.list_cursor(
            limit=limit,
            before_id=_cursor_id(cursor),
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            ref_type=ref_type,
            ref_id=ref_id,
            flow_type=flow_type,
        )
        return build_cursor_page([_flow_view(row) for row in rows], limit)

    async def export_flows(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        sku_id: int | None,
        warehouse_id: int | None,
        ref_type: str | None,
        ref_id: int | None,
    ) -> FilePayload:
        cap = settings.order_export_max_rows
        rows = await self.flows.list_cursor(
            limit=cap + 1,
            before_id=None,
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            ref_type=ref_type,
            ref_id=ref_id,
            flow_type=None,
        )
        truncated = len(rows) > cap
        visible = rows[:cap]
        headers = ["流水", "时间", "SKU", "仓库", "类型", "数量", "来源类型", "来源单号", "变动前", "变动后"]
        table = [
            [
                str(row.id),
                row.created_at.isoformat(),
                str(row.sku_id),
                str(row.warehouse_id),
                row.flow_type,
                str(row.quantity),
                row.ref_type,
                "" if row.ref_id is None else str(row.ref_id),
                str(row.before_qty),
                str(row.after_qty),
            ]
            for row in visible
        ]
        payload = build_xlsx(headers, table, sheet_name="flows")
        await self.audit.append_action(
            tenant_id=tenant_id,
            action=AuditAction.DATA_EXPORT,
            resource="inventory_flow",
            user_id=actor_id,
            after={"row_count": len(visible), "truncated": truncated},
        )
        return FilePayload(
            filename="inventory-flows.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content_base64=base64.b64encode(payload).decode("ascii"),
            row_count=len(visible),
            truncated=truncated,
        )

    async def reserve_order(self, order_id: int) -> None:
        """付款预占。SKU 未匹配则跳过。不够的数量记 SHORT，不把订单同步打失败。"""

        for item in await self.orders.list_items(order_id):
            if not should_reserve_line(item.sku_id, item.quantity):
                continue
            existing = await self.holds.get_for_item(item.id)
            if existing is not None and existing.status in _OPEN_OR_DONE:
                continue
            taken, short = await self._reserve_quantity(
                item.sku_id,
                item.quantity,
                ref_type="ORDER",
                ref_id=item.order_id,
            )
            status = "OPEN" if taken else "SHORT"
            if existing is None:
                self.session.add(
                    InventoryHold(
                        tenant_id=item.tenant_id,
                        order_id=item.order_id,
                        order_item_id=item.id,
                        sku_id=item.sku_id,
                        quantity=sum(piece.quantity for piece in taken),
                        short_qty=short,
                        allocations=_alloc_maps(taken),
                        status=status,
                    )
                )
            else:
                existing.quantity = sum(piece.quantity for piece in taken)
                existing.short_qty = short
                existing.allocations = _alloc_maps(taken)
                existing.status = status
            self._touched.add(item.sku_id)
        await self.session.flush()
        await self._flush_enqueue()

    async def release_order(self, order_id: int) -> None:
        """未发货取消时释放预占。已经出库的不在这里回滚。"""

        for hold in await self.holds.list_for_order(order_id):
            if hold.status != "OPEN":
                continue
            for alloc in _read_allocs(hold.allocations):
                row = await self.inventories.get_pair(hold.sku_id, alloc.warehouse_id)
                if row is None:
                    continue
                await self._commit_change(
                    row,
                    occupied_delta=-alloc.quantity,
                    flow_type="RELEASE",
                    quantity=alloc.quantity,
                    before_name="occupied",
                    ref_type="ORDER",
                    ref_id=hold.order_id,
                    actor_id=None,
                )
            hold.status = "RELEASED"
            self._touched.add(hold.sku_id)
        await self.session.flush()
        await self._flush_enqueue()

    async def ship_order(self, order_id: int) -> None:
        """发货把预占转成出库。没有预占记录时直接扣实物，仍然不许扣成负数。"""

        for item in await self.orders.list_items(order_id):
            if not should_reserve_line(item.sku_id, item.quantity):
                continue
            hold = await self.holds.get_for_item(item.id)
            if hold is not None and hold.status == "SHIPPED":
                continue
            if hold is not None and hold.status == "OPEN":
                for alloc in _read_allocs(hold.allocations):
                    row = await self.inventories.get_pair(item.sku_id, alloc.warehouse_id)
                    if row is None:
                        raise NotFoundError("库存不存在")
                    await self._commit_change(
                        row,
                        available_delta=-alloc.quantity,
                        occupied_delta=-alloc.quantity,
                        flow_type="SHIP",
                        quantity=alloc.quantity,
                        before_name="available",
                        ref_type="ORDER",
                        ref_id=item.order_id,
                        actor_id=None,
                    )
                hold.status = "SHIPPED"
                self._touched.add(item.sku_id)
                continue
            if hold is not None and hold.status == "SHORT":
                hold.status = "SHIPPED"
                continue
            taken, _short = await self._outbound_quantity(
                item.sku_id,
                item.quantity,
                ref_type="ORDER",
                ref_id=item.order_id,
            )
            self.session.add(
                InventoryHold(
                    tenant_id=item.tenant_id,
                    order_id=item.order_id,
                    order_item_id=item.id,
                    sku_id=item.sku_id,
                    quantity=sum(piece.quantity for piece in taken),
                    short_qty=item.quantity - sum(piece.quantity for piece in taken),
                    allocations=_alloc_maps(taken),
                    status="SHIPPED",
                )
            )
            self._touched.add(item.sku_id)
        await self.session.flush()
        await self._flush_enqueue()

    async def receive_return(self, row: ReturnOrder, *, actor_id: int | None) -> None:
        """退货入库。可售加实物，否则加残次，然后把 DEFERRED 写成 POSTED。"""

        if row.restock_status != "DEFERRED":
            return
        bucket = restock_bucket(sellable=row.restock_sellable)
        for item in await self.orders.list_items(row.order_id):
            if not should_reserve_line(item.sku_id, item.quantity):
                continue
            targets = await self._return_targets(item)
            for alloc in targets:
                balance = await self._ensure(
                    item.sku_id,
                    alloc.warehouse_id,
                    tenant_id=row.tenant_id,
                    actor_id=actor_id,
                )
                if bucket == "available":
                    await self._commit_change(
                        balance,
                        available_delta=alloc.quantity,
                        flow_type="RETURN_IN",
                        quantity=alloc.quantity,
                        before_name="available",
                        ref_type="RETURN",
                        ref_id=row.id,
                        actor_id=actor_id,
                    )
                else:
                    await self._commit_change(
                        balance,
                        defective_delta=alloc.quantity,
                        flow_type="RETURN_IN",
                        quantity=alloc.quantity,
                        before_name="defective",
                        ref_type="RETURN",
                        ref_id=row.id,
                        actor_id=actor_id,
                    )
            self._touched.add(item.sku_id)
        row.restock_status = mark_posted(row.restock_status)
        await self.session.flush()
        await self._flush_enqueue()

    async def upsert_safety(self, payload: SafetyStockWrite, *, tenant_id: int, actor_id: int) -> SafetyStockView:
        if payload.platform_code not in SUPPORTED_PLATFORMS:
            raise ParamInvalidError("平台不支持")
        sku = await self.skus.get_or_404(payload.sku_id)
        row = await self.safety.get_active(payload.sku_id, payload.platform_code)
        if row is None:
            row = PlatformSafetyStock(
                tenant_id=tenant_id,
                sku_id=payload.sku_id,
                platform_code=payload.platform_code,
                quantity=payload.quantity,
                lead_time_days=payload.lead_time_days,
                cover_days=payload.cover_days,
                created_by=actor_id,
                updated_by=actor_id,
            )
            await self.safety.add(row)
        else:
            row.quantity = payload.quantity
            row.lead_time_days = payload.lead_time_days
            row.cover_days = payload.cover_days
            row.updated_by = actor_id
            await self.session.flush()
        return _safety_view(row, sku.sku_code)

    async def list_safety(self, *, limit: int, cursor: str | None) -> PageData[SafetyStockView]:
        rows = await self.safety.list_cursor(limit=limit, before_id=_cursor_id(cursor))
        return build_cursor_page([_safety_view(row, sku_code) for row, sku_code in rows], limit)

    async def replenishments(self, *, window_days: int) -> list[ReplenishmentView]:
        if window_days < 1:
            raise ParamInvalidError("统计窗口至少 1 天")
        configured = await self.safety.list_active()
        if not configured:
            return []
        since = datetime.now(UTC) - timedelta(days=window_days)
        sales = await self.inventories.sales_since(since)
        totals = await self.inventories.totals_by_sku([row.sku_id for row, _code in configured])
        today = datetime.now(UTC).date()
        views: list[ReplenishmentView] = []
        for row, sku_code in configured:
            sold = sales.get((row.sku_id, row.platform_code), 0)
            movable, transit = totals.get(row.sku_id, (0, 0))
            suggestion = suggest_replenishment(
                sold=sold,
                window_days=window_days,
                lead_time_days=row.lead_time_days,
                cover_days=row.cover_days,
                safety=row.quantity,
                movable=movable,
                in_transit=transit,
                today=today,
            )
            views.append(
                ReplenishmentView(
                    sku_id=row.sku_id,
                    sku_code=sku_code,
                    platform_code=row.platform_code,
                    safety=row.quantity,
                    lead_time_days=row.lead_time_days,
                    cover_days=row.cover_days,
                    sold=sold,
                    window_days=window_days,
                    movable=movable,
                    in_transit=transit,
                    daily_sales=suggestion.daily_sales,
                    suggested_qty=suggestion.suggested_qty,
                    order_on=suggestion.order_on,
                )
            )
        return views

    async def list_transfers(self, *, limit: int, cursor: str | None) -> PageData[TransferView]:
        rows = await self.transfers.list_cursor(limit=limit, before_id=_cursor_id(cursor))
        views = [await self._transfer_view(row) for row in rows]
        return build_cursor_page(views, limit)

    async def create_transfer(self, payload: TransferCreate, *, tenant_id: int, actor_id: int) -> TransferView:
        if payload.from_warehouse_id == payload.to_warehouse_id:
            raise TransferWarehouseError()
        await self.warehouses.get_or_404(payload.from_warehouse_id)
        await self.warehouses.get_or_404(payload.to_warehouse_id)
        for line in payload.lines:
            await self.skus.get_or_404(line.sku_id)
        row = StockTransfer(
            tenant_id=tenant_id,
            from_warehouse_id=payload.from_warehouse_id,
            to_warehouse_id=payload.to_warehouse_id,
            status="DRAFT",
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.transfers.add(row)
        for line in payload.lines:
            await self.transfers.add_line(
                StockTransferLine(
                    tenant_id=tenant_id,
                    transfer_id=row.id,
                    sku_id=line.sku_id,
                    quantity=line.quantity,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
        return await self._transfer_view(row)

    async def ship_transfer(self, transfer_id: int, *, tenant_id: int, actor_id: int) -> TransferView:
        row = await self.transfers.get_or_404(transfer_id)
        if row.status != "DRAFT":
            raise TransferStateError()
        lines = await self.transfers.lines_for(row.id)
        if not await self.transfers.transition(row.id, "DRAFT", "IN_TRANSIT", actor_id):
            raise TransferStateError()
        await self.session.refresh(row)
        for line in lines:
            source_move, dest_move = ship_moves(line.quantity)
            source = await self._ensure(line.sku_id, row.from_warehouse_id, tenant_id=tenant_id, actor_id=actor_id)
            dest = await self._ensure(line.sku_id, row.to_warehouse_id, tenant_id=tenant_id, actor_id=actor_id)
            await self._move_leg(source, source_move, quantity=line.quantity, ref_id=row.id, actor_id=actor_id)
            await self._move_leg(dest, dest_move, quantity=line.quantity, ref_id=row.id, actor_id=actor_id)
        await self._flush_enqueue()
        return await self._transfer_view(row)

    async def receive_transfer(self, transfer_id: int, *, tenant_id: int, actor_id: int) -> TransferView:
        row = await self.transfers.get_or_404(transfer_id)
        if row.status != "IN_TRANSIT":
            raise TransferStateError()
        lines = await self.transfers.lines_for(row.id)
        if not await self.transfers.transition(row.id, "IN_TRANSIT", "RECEIVED", actor_id):
            raise TransferStateError()
        await self.session.refresh(row)
        for line in lines:
            dest = await self._ensure(line.sku_id, row.to_warehouse_id, tenant_id=tenant_id, actor_id=actor_id)
            await self._move_leg(
                dest,
                receive_move(line.quantity),
                quantity=line.quantity,
                ref_id=row.id,
                actor_id=actor_id,
            )
        await self._flush_enqueue()
        return await self._transfer_view(row)

    async def cancel_transfer(self, transfer_id: int, *, tenant_id: int, actor_id: int) -> TransferView:
        row = await self.transfers.get_or_404(transfer_id)
        lines = await self.transfers.lines_for(row.id)
        if row.status == "DRAFT":
            if not await self.transfers.transition(row.id, "DRAFT", "CANCELLED", actor_id):
                raise TransferStateError()
            await self.session.refresh(row)
            return await self._transfer_view(row)
        if row.status != "IN_TRANSIT":
            raise TransferStateError()
        if not await self.transfers.transition(row.id, "IN_TRANSIT", "CANCELLED", actor_id):
            raise TransferStateError()
        await self.session.refresh(row)
        for line in lines:
            dest_move, source_move = cancel_transit_moves(line.quantity)
            dest = await self._ensure(line.sku_id, row.to_warehouse_id, tenant_id=tenant_id, actor_id=actor_id)
            source = await self._ensure(line.sku_id, row.from_warehouse_id, tenant_id=tenant_id, actor_id=actor_id)
            await self._move_leg(dest, dest_move, quantity=line.quantity, ref_id=row.id, actor_id=actor_id)
            await self._move_leg(source, source_move, quantity=line.quantity, ref_id=row.id, actor_id=actor_id)
        await self._flush_enqueue()
        return await self._transfer_view(row)

    async def list_takings(self, *, limit: int, cursor: str | None) -> PageData[StockTakingView]:
        rows = await self.takings.list_cursor(limit=limit, before_id=_cursor_id(cursor))
        views = [await self._taking_view(row) for row in rows]
        return build_cursor_page(views, limit)

    async def create_taking(self, payload: StockTakingCreate, *, tenant_id: int, actor_id: int) -> StockTakingView:
        await self.warehouses.get_or_404(payload.warehouse_id)
        balances = await self.inventories.list_by_warehouse(payload.warehouse_id)
        if not balances:
            raise ParamInvalidError("该仓库没有库存可盘点")
        row = StockTaking(
            tenant_id=tenant_id,
            warehouse_id=payload.warehouse_id,
            status="DRAFT",
            diff_summary={},
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.takings.add(row)
        for balance in balances:
            await self.takings.add_line(
                StockTakingLine(
                    tenant_id=tenant_id,
                    taking_id=row.id,
                    sku_id=balance.sku_id,
                    book_qty=balance.available,
                    counted_qty=None,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
        return await self._taking_view(row)

    async def record_counts(
        self,
        taking_id: int,
        payload: StockTakingCounts,
        *,
        actor_id: int,
    ) -> StockTakingView:
        row = await self.takings.get_or_404(taking_id)
        if row.status != "DRAFT":
            raise StockTakingStateError()
        for item in payload.lines:
            line = await self.takings.line_for_sku(row.id, item.sku_id)
            if line is None:
                raise StockTakingStateError("盘点行不存在")
            line.counted_qty = item.counted_qty
            line.updated_by = actor_id
        row.updated_by = actor_id
        await self.session.flush()
        return await self._taking_view(row)

    async def post_taking(self, taking_id: int, *, tenant_id: int, actor_id: int) -> StockTakingView:
        row = await self.takings.get_or_404(taking_id)
        if row.status != "DRAFT":
            raise StockTakingStateError()
        lines = await self.takings.lines_for(row.id)
        if any(line.counted_qty is None for line in lines):
            raise StockTakingStateError("还有 SKU 没有录入实盘")
        gain = 0
        loss = 0
        planned: list[tuple[int, int]] = []
        for line in lines:
            counted = line.counted_qty
            if counted is None:
                raise StockTakingStateError("还有 SKU 没有录入实盘")
            delta = taking_delta(line.book_qty, counted)
            inventory = await self.inventories.get_pair(line.sku_id, row.warehouse_id)
            if inventory is None:
                raise NotFoundError("库存不存在")
            if not taking_fits(inventory.available, inventory.occupied, delta):
                raise InventoryInsufficientError()
            if delta > 0:
                gain += delta
            elif delta < 0:
                loss += -delta
            planned.append((line.sku_id, delta))
        if not await self.takings.transition(row.id, "DRAFT", "POSTED", actor_id):
            raise StockTakingStateError()
        await self.session.refresh(row)
        for sku_id, delta in planned:
            if delta == 0:
                continue
            await self.adjust(
                InventoryAdjust(
                    sku_id=sku_id,
                    warehouse_id=row.warehouse_id,
                    kind="ADJUST",
                    quantity=delta,
                    ref_type="STOCK_TAKING",
                    ref_id=row.id,
                ),
                tenant_id=tenant_id,
                actor_id=actor_id,
            )
        row.diff_summary = {"lines": len(lines), "gain_qty": gain, "loss_qty": loss}
        row.updated_by = actor_id
        await self.session.flush()
        return await self._taking_view(row)

    async def cancel_taking(self, taking_id: int, *, actor_id: int) -> StockTakingView:
        row = await self.takings.get_or_404(taking_id)
        if row.status != "DRAFT":
            raise StockTakingStateError()
        if not await self.takings.transition(row.id, "DRAFT", "CANCELLED", actor_id):
            raise StockTakingStateError()
        await self.session.refresh(row)
        return await self._taking_view(row)

    async def turnover(self, *, dimension: str, window_days: int, include_cost: bool) -> list[TurnoverView]:
        if dimension not in ("sku", "warehouse", "platform"):
            raise ParamInvalidError("周转维度不支持")
        if window_days < 1:
            raise ParamInvalidError("统计窗口至少 1 天")
        since = datetime.now(UTC) - timedelta(days=window_days)
        physical = await self.inventories.physical_rows()
        if dimension == "warehouse":
            shipped = await self.flows.shipped_since(since)
            views: list[TurnoverView] = []
            for row in physical:
                view = _turnover_view(
                    dimension="warehouse",
                    sku_id=row.sku_id,
                    sku_code=row.sku_code,
                    on_hand=row.on_hand,
                    sold=shipped.get((row.sku_id, row.warehouse_id), 0),
                    window_days=window_days,
                    unit_cost=row.purchase_price,
                    currency=row.currency,
                    include_cost=include_cost,
                    warehouse_id=row.warehouse_id,
                    warehouse_name=row.warehouse_name,
                )
                if view is not None:
                    views.append(view)
            return views
        on_hand: dict[int, int] = defaultdict(int)
        meta: dict[int, PhysicalRow] = {}
        for row in physical:
            on_hand[row.sku_id] += row.on_hand
            meta[row.sku_id] = row
        if dimension == "platform":
            sales = await self.inventories.sales_since(since)
            views = []
            for sku_id, hand in on_hand.items():
                row = meta[sku_id]
                for platform in SUPPORTED_PLATFORMS:
                    view = _turnover_view(
                        dimension="platform",
                        sku_id=sku_id,
                        sku_code=row.sku_code,
                        on_hand=hand,
                        sold=sales.get((sku_id, platform), 0),
                        window_days=window_days,
                        unit_cost=row.purchase_price,
                        currency=row.currency,
                        include_cost=include_cost,
                        platform_code=platform,
                    )
                    if view is not None:
                        views.append(view)
            return views
        sales = await self.inventories.sales_since(since)
        sold_by_sku: dict[int, int] = defaultdict(int)
        for (sku_id, _platform), qty in sales.items():
            sold_by_sku[sku_id] += qty
        views = []
        for sku_id, hand in on_hand.items():
            row = meta[sku_id]
            view = _turnover_view(
                dimension="sku",
                sku_id=sku_id,
                sku_code=row.sku_code,
                on_hand=hand,
                sold=sold_by_sku.get(sku_id, 0),
                window_days=window_days,
                unit_cost=row.purchase_price,
                currency=row.currency,
                include_cost=include_cost,
            )
            if view is not None:
                views.append(view)
        return views

    async def list_push_logs(
        self,
        *,
        limit: int,
        cursor: str | None,
        sku_id: int | None,
        shop_id: int | None,
    ) -> PageData[PushLogView]:
        rows = await self.push_logs.list_cursor(
            limit=limit,
            before_id=_cursor_id(cursor),
            sku_id=sku_id,
            shop_id=shop_id,
        )
        return build_cursor_page([_push_view(row) for row in rows], limit)

    async def push_sku(self, sku_id: int, *, sweep: bool, retry_count: int = 0) -> PushRun:
        """按店铺把可动用库存减去平台水位后回传。巡检只对超过滞后阈值的店铺回传 0。"""

        await self.skus.get_or_404(sku_id)
        pairs = await self.inventories.list_for_sku(sku_id)
        free = sum(row.available - row.occupied for row, _warehouse in pairs)
        listings = await self.listings.list_linked_for_sku(sku_id)
        grouped: dict[int, list[Listing]] = defaultdict(list)
        for listing in listings:
            if listing.platform_sku_id:
                grouped[listing.shop_id].append(listing)
        written: list[InventoryPushLog] = []
        zeroed = 0
        for shop_id, group in grouped.items():
            shop = await self.shops.get(shop_id)
            if shop is None:
                continue
            spec = await RateLimitConfigService(self.session).resolve(shop.platform_code)
            safety = await self.safety.get_active(sku_id, shop.platform_code)
            safety_qty = DEFAULT_SAFE_STOCK if safety is None else safety.quantity
            quantity = platform_push_quantity(free, safety_qty)
            anchor = min(ensure_utc(item.updated_at) for item in group)
            last_success = await self.push_logs.latest_success_at(shop_id, sku_id)
            lagged = should_push_zero(
                last_success_at=None if last_success is None else ensure_utc(last_success),
                anchor=anchor,
                now=datetime.now(UTC),
                lag_seconds=spec.stock_push_lag_seconds,
            )
            status = "SUCCESS"
            if sweep and not lagged:
                continue
            if sweep and lagged:
                quantity = 0
                status = "LAGGED_ZERO"
            updates = [
                InventoryUpdate(platform_sku_id=item.platform_sku_id, available=quantity)
                for item in group
                if item.platform_sku_id
            ]
            try:
                for chunk in chunk_updates(updates, spec.batch_limit):
                    await self._send(shop, chunk)
            except AdapterError as exc:
                failed = await self._push_row(
                    shop,
                    sku_id,
                    quantity=quantity,
                    status="FAILED",
                    retry_count=retry_count,
                    message=str(exc)[:512],
                )
                written.append(failed)
                return PushRun(
                    logs=written,
                    retry=exc.decision in {RetryDecision.RETRY, RetryDecision.RETRY_AFTER, RetryDecision.DEAD_LETTER},
                    error=str(exc),
                    decision=exc.decision,
                    retry_after_seconds=exc.retry_after_seconds,
                    zeroed=zeroed,
                )
            logged = await self._push_row(
                shop,
                sku_id,
                quantity=quantity,
                status=status,
                retry_count=retry_count,
                message="滞后已降为 0" if status == "LAGGED_ZERO" else "",
            )
            written.append(logged)
            if status == "LAGGED_ZERO":
                zeroed += 1
        return PushRun(logs=written, zeroed=zeroed)

    async def sweep_lag(self) -> PushRun:
        listings = await self.listings.list_linked(limit=_SWEEP_LISTING_LIMIT)
        seen: set[int] = set()
        logs: list[InventoryPushLog] = []
        zeroed = 0
        for listing in listings:
            if listing.sku_id in seen:
                continue
            seen.add(listing.sku_id)
            run = await self.push_sku(listing.sku_id, sweep=True)
            logs.extend(run.logs)
            zeroed += run.zeroed
            if run.retry:
                return PushRun(
                    logs=logs,
                    retry=True,
                    error=run.error,
                    decision=run.decision,
                    retry_after_seconds=run.retry_after_seconds,
                    zeroed=zeroed,
                )
        return PushRun(logs=logs, zeroed=zeroed)

    async def _send(self, shop: Shop, items: list[InventoryUpdate]) -> None:
        if not items:
            return
        spec = await RateLimitConfigService(self.session).resolve(shop.platform_code)
        try:
            slot = await get_rate_limiter().try_acquire(shop.platform_code, str(shop.id), spec)
        except StoreUnavailable as exc:
            raise AdapterError("限流状态不可用", platform=shop.platform_code, decision=RetryDecision.RETRY) from exc
        if not slot.allowed:
            raise AdapterError(
                "触发平台限流",
                platform=shop.platform_code,
                http_status=429,
                decision=RetryDecision.RETRY_AFTER,
                retry_after_seconds=max(1, int(slot.retry_after_seconds)),
            )
        result = await self.stock.update(shop, items)
        if result.failed:
            raise AdapterError("平台拒绝库存回传", platform=shop.platform_code, decision=RetryDecision.RETRY)

    async def _push_row(
        self,
        shop: Shop,
        sku_id: int,
        *,
        quantity: int,
        status: str,
        retry_count: int,
        message: str,
    ) -> InventoryPushLog:
        tenant_id = get_tenant_id()
        if tenant_id is None:
            raise AppError("缺少租户上下文", code=ErrorCode.CROSS_TENANT_DENIED)
        row = InventoryPushLog(
            tenant_id=int(tenant_id),
            shop_id=shop.id,
            sku_id=sku_id,
            platform_code=shop.platform_code,
            quantity=quantity,
            status=status,
            retry_count=retry_count,
            message=message,
        )
        await self.push_logs.add(row)
        return row

    async def _return_targets(self, item: OrderItem) -> list[Allocation]:
        hold = await self.holds.get_for_item(item.id)
        if hold is not None:
            allocs = _read_allocs(hold.allocations)
            if allocs:
                return allocs
        warehouse = await self.warehouses.get_default()
        if warehouse is None:
            warehouse = await self.warehouses.first_fulfillment()
        if warehouse is None:
            raise ParamInvalidError("请先创建仓库，才能把退货记入库存")
        return [Allocation(warehouse_id=warehouse.id, quantity=item.quantity)]

    async def _reserve_quantity(
        self,
        sku_id: int,
        quantity: int,
        *,
        ref_type: str,
        ref_id: int,
    ) -> tuple[list[Allocation], int]:
        return await self._draw(sku_id, quantity, ref_type=ref_type, ref_id=ref_id, mode="reserve")

    async def _outbound_quantity(
        self,
        sku_id: int,
        quantity: int,
        *,
        ref_type: str,
        ref_id: int,
    ) -> tuple[list[Allocation], int]:
        return await self._draw(sku_id, quantity, ref_type=ref_type, ref_id=ref_id, mode="outbound")

    async def _draw(
        self,
        sku_id: int,
        quantity: int,
        *,
        ref_type: str,
        ref_id: int,
        mode: str,
    ) -> tuple[list[Allocation], int]:
        left = quantity
        taken: list[Allocation] = []
        for _ in range(_VERSION_RETRIES):
            if left == 0:
                break
            pairs = await self.inventories.list_for_sku(sku_id)
            bins = [
                BinView(
                    warehouse_id=row.warehouse_id,
                    available=row.available,
                    occupied=row.occupied,
                    is_default=warehouse.is_default,
                    warehouse_type=warehouse.warehouse_type,
                )
                for row, warehouse in pairs
            ]
            planned, _short = plan_allocations(bins, left)
            if not planned:
                break
            conflict = False
            for alloc in planned:
                row = next(item for item, _warehouse in pairs if item.warehouse_id == alloc.warehouse_id)
                if mode == "reserve":
                    deltas = {"occupied_delta": alloc.quantity}
                    flow_type = "RESERVE"
                    before_name = "occupied"
                else:
                    deltas = {"available_delta": -alloc.quantity}
                    flow_type = "SHIP"
                    before_name = "available"
                status, fresh = await self._apply_once(row, **deltas)
                if status == "applied" and fresh is not None:
                    await self._append_flow(
                        fresh,
                        flow_type=flow_type,
                        quantity=alloc.quantity,
                        before_qty=getattr(row, before_name),
                        after_qty=getattr(fresh, before_name),
                        ref_type=ref_type,
                        ref_id=ref_id,
                        actor_id=None,
                    )
                    taken.append(alloc)
                    left -= alloc.quantity
                    continue
                conflict = True
                break
            if not conflict:
                break
        return taken, left

    async def _commit_change(
        self,
        row: Inventory,
        *,
        flow_type: str,
        quantity: int,
        before_name: str,
        ref_type: str,
        ref_id: int | None,
        actor_id: int | None,
        available_delta: int = 0,
        occupied_delta: int = 0,
        defective_delta: int = 0,
        in_transit_delta: int = 0,
    ) -> Inventory:
        current = row
        for _ in range(_VERSION_RETRIES):
            before_qty = getattr(current, before_name)
            status, fresh = await self._apply_once(
                current,
                available_delta=available_delta,
                occupied_delta=occupied_delta,
                defective_delta=defective_delta,
                in_transit_delta=in_transit_delta,
            )
            if status == "applied" and fresh is not None:
                await self._append_flow(
                    fresh,
                    flow_type=flow_type,
                    quantity=quantity,
                    before_qty=before_qty,
                    after_qty=getattr(fresh, before_name),
                    ref_type=ref_type,
                    ref_id=ref_id,
                    actor_id=actor_id,
                )
                self._touched.add(fresh.sku_id)
                return fresh
            if status == "conflict" and fresh is not None:
                current = fresh
                continue
            if status == "insufficient":
                raise InventoryInsufficientError()
            raise NotFoundError("库存不存在")
        raise InventoryConflictError()

    async def _apply_once(
        self,
        row: Inventory,
        *,
        available_delta: int = 0,
        occupied_delta: int = 0,
        defective_delta: int = 0,
        in_transit_delta: int = 0,
    ) -> tuple[str, Inventory | None]:
        status = await self.inventories.apply_change(
            row.id,
            version=row.version,
            available_delta=available_delta,
            occupied_delta=occupied_delta,
            defective_delta=defective_delta,
            in_transit_delta=in_transit_delta,
        )
        if status == "applied":
            fresh = await self.inventories.get(row.id)
            if fresh is not None:
                await self.session.refresh(fresh)
            return "applied", fresh
        if status == "conflict":
            fresh = await self.inventories.get(row.id)
            if fresh is not None:
                await self.session.refresh(fresh)
            return "conflict", fresh
        return status, None

    async def _append_flow(
        self,
        row: Inventory,
        *,
        flow_type: str,
        quantity: int,
        before_qty: int,
        after_qty: int,
        ref_type: str,
        ref_id: int | None,
        actor_id: int | None,
    ) -> None:
        self.session.add(
            InventoryFlow(
                tenant_id=row.tenant_id,
                created_at=datetime.now(UTC),
                inventory_id=row.id,
                sku_id=row.sku_id,
                warehouse_id=row.warehouse_id,
                flow_type=flow_type,
                quantity=quantity,
                ref_type=ref_type,
                ref_id=ref_id,
                before_qty=before_qty,
                after_qty=after_qty,
                created_by=actor_id,
            )
        )
        await self.session.flush()

    async def _ensure(self, sku_id: int, warehouse_id: int, *, tenant_id: int, actor_id: int | None) -> Inventory:
        await self.skus.get_or_404(sku_id)
        await self.warehouses.get_or_404(warehouse_id)
        found = await self.inventories.get_pair(sku_id, warehouse_id)
        if found is not None:
            return found
        row = Inventory(
            tenant_id=tenant_id,
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            safe_stock=DEFAULT_SAFE_STOCK,
            created_by=actor_id,
            updated_by=actor_id,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            found = await self.inventories.get_pair(sku_id, warehouse_id)
            if found is None:
                raise
            return found
        return row

    async def _move_leg(
        self,
        inventory: Inventory,
        move: StockMove,
        *,
        quantity: int,
        ref_id: int,
        actor_id: int,
    ) -> None:
        await self._commit_change(
            inventory,
            flow_type=move.flow_type,
            quantity=quantity,
            before_name=move.bucket,
            ref_type="TRANSFER",
            ref_id=ref_id,
            actor_id=actor_id,
            available_delta=move.available_delta,
            in_transit_delta=move.in_transit_delta,
        )

    async def _transfer_view(self, row: StockTransfer) -> TransferView:
        source = await self.warehouses.get_or_404(row.from_warehouse_id)
        dest = await self.warehouses.get_or_404(row.to_warehouse_id)
        lines: list[TransferLineView] = []
        for line in await self.transfers.lines_for(row.id):
            sku = await self.skus.get_or_404(line.sku_id)
            lines.append(TransferLineView(sku_id=line.sku_id, sku_code=sku.sku_code, quantity=line.quantity))
        return TransferView(
            id=row.id,
            from_warehouse_id=row.from_warehouse_id,
            to_warehouse_id=row.to_warehouse_id,
            from_warehouse_name=source.name,
            to_warehouse_name=dest.name,
            status=row.status,
            lines=lines,
            created_at=row.created_at,
        )

    async def _taking_view(self, row: StockTaking) -> StockTakingView:
        warehouse = await self.warehouses.get_or_404(row.warehouse_id)
        lines: list[StockTakingLineView] = []
        for line in await self.takings.lines_for(row.id):
            sku = await self.skus.get_or_404(line.sku_id)
            lines.append(
                StockTakingLineView(
                    sku_id=line.sku_id,
                    sku_code=sku.sku_code,
                    book_qty=line.book_qty,
                    counted_qty=line.counted_qty,
                )
            )
        summary = {str(key): int(value) for key, value in row.diff_summary.items()}
        return StockTakingView(
            id=row.id,
            warehouse_id=row.warehouse_id,
            warehouse_name=warehouse.name,
            status=row.status,
            diff_summary=summary,
            lines=lines,
            created_at=row.created_at,
        )

    async def _view_for(self, row: Inventory) -> InventoryView:
        sku = await self.skus.get_or_404(row.sku_id)
        warehouse = await self.warehouses.get_or_404(row.warehouse_id)
        return InventoryView(
            id=row.id,
            sku_id=row.sku_id,
            sku_code=sku.sku_code,
            warehouse_id=row.warehouse_id,
            warehouse_name=warehouse.name,
            warehouse_type=warehouse.warehouse_type,
            available=row.available,
            occupied=row.occupied,
            in_transit=row.in_transit,
            defective=row.defective,
            safe_stock=row.safe_stock,
            sellable=sellable_qty(row.available, row.occupied, row.safe_stock),
            version=row.version,
        )

    async def _flush_enqueue(self) -> None:
        tenant_id = get_tenant_id()
        if tenant_id is None or not self._touched:
            self._touched.clear()
            return
        from app.tasks.inventory import enqueue_inventory_push

        for sku_id in list(self._touched):
            try:
                enqueue_inventory_push(int(tenant_id), sku_id)
            except Exception:
                log.warning("inventory_push_enqueue_failed", sku_id=sku_id)
        self._touched.clear()


def _turnover_view(
    *,
    dimension: Literal["sku", "warehouse", "platform"],
    sku_id: int,
    sku_code: str,
    on_hand: int,
    sold: int,
    window_days: int,
    unit_cost: Decimal | None,
    currency: str | None,
    include_cost: bool,
    warehouse_id: int | None = None,
    warehouse_name: str | None = None,
    platform_code: str | None = None,
) -> TurnoverView | None:
    if on_hand == 0 and sold == 0:
        return None
    amount = dead_stock_amount(on_hand, sold, unit_cost) if include_cost else None
    return TurnoverView(
        dimension=dimension,
        sku_id=sku_id,
        sku_code=sku_code,
        warehouse_id=warehouse_id,
        warehouse_name=warehouse_name,
        platform_code=platform_code,
        on_hand=on_hand,
        sold=sold,
        window_days=window_days,
        turnover_days=turnover_days(on_hand, sold, window_days),
        dead=sold == 0 and on_hand > 0,
        dead_stock_amount=amount,
        currency=currency if amount is not None else None,
    )


def _adjust_deltas(kind: str, quantity: int) -> tuple[dict[str, int], str, str]:
    if kind == "INBOUND":
        return {"available_delta": quantity}, "INBOUND", "available"
    if kind == "OUTBOUND":
        return {"available_delta": -quantity}, "OUTBOUND", "available"
    if kind == "TO_DEFECTIVE":
        return {"available_delta": -quantity, "defective_delta": quantity}, "ADJUST", "available"
    return {"available_delta": quantity}, "ADJUST", "available"


def _warehouse_view(row: Warehouse) -> WarehouseView:
    return WarehouseView(
        id=row.id,
        name=row.name,
        warehouse_type=row.warehouse_type,
        country=row.country,
        address=row.address,
        external_code=row.external_code,
        is_default=row.is_default,
    )


def _inventory_view(row: Any) -> InventoryView:
    return InventoryView(
        id=int(row.id),
        sku_id=int(row.sku_id),
        sku_code=str(row.sku_code),
        warehouse_id=int(row.warehouse_id),
        warehouse_name=str(row.warehouse_name),
        warehouse_type=str(row.warehouse_type),
        available=int(row.available),
        occupied=int(row.occupied),
        in_transit=int(row.in_transit),
        defective=int(row.defective),
        safe_stock=int(row.safe_stock),
        sellable=int(row.sellable),
        version=int(row.version),
    )


def _flow_view(row: InventoryFlow) -> FlowView:
    return FlowView(
        id=row.id,
        created_at=row.created_at,
        sku_id=row.sku_id,
        warehouse_id=row.warehouse_id,
        flow_type=row.flow_type,
        quantity=row.quantity,
        ref_type=row.ref_type,
        ref_id=row.ref_id,
        before_qty=row.before_qty,
        after_qty=row.after_qty,
    )


def _safety_view(row: PlatformSafetyStock, sku_code: str) -> SafetyStockView:
    return SafetyStockView(
        id=row.id,
        sku_id=row.sku_id,
        sku_code=sku_code,
        platform_code=row.platform_code,
        quantity=row.quantity,
        lead_time_days=row.lead_time_days,
        cover_days=row.cover_days,
    )


def _push_view(row: InventoryPushLog) -> PushLogView:
    return PushLogView(
        id=row.id,
        shop_id=row.shop_id,
        sku_id=row.sku_id,
        platform_code=row.platform_code,
        quantity=row.quantity,
        status=row.status,
        retry_count=row.retry_count,
        message=row.message,
        created_at=row.created_at,
    )


__all__ = ["AdapterStock", "InventoryService", "PushRun", "StockPort"]
