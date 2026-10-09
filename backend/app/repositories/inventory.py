"""库存余额、流水和回传日志。只有这一层写 SQL。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, NamedTuple

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.context import get_tenant_id, require_tenant_id
from app.core.errors import AppError, CrossTenantError, ErrorCode
from app.models.inventory import (
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
from app.models.order import OrderItem, SalesOrder
from app.models.product import Sku
from app.repositories.base import BaseRepository
from app.repositories.listing import _unique_violation


class WarehouseRepository(BaseRepository[Warehouse]):
    model = Warehouse

    async def add(self, obj: Warehouse) -> Warehouse:
        try:
            return await super().add(obj)
        except IntegrityError as exc:
            self._reraise(exc)
            raise

    async def save(self, obj: Warehouse) -> Warehouse:
        self.assert_tenant_owned(obj)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            self._reraise(exc)
            raise
        return obj

    async def clear_default(self, *, except_id: int | None) -> None:
        stmt = update(Warehouse).where(
            Warehouse.tenant_id == require_tenant_id(),
            Warehouse.is_default.is_(True),
            Warehouse.deleted_at.is_(None),
        )
        if except_id is not None:
            stmt = stmt.where(Warehouse.id != except_id)
        await self.session.execute(stmt.values(is_default=False))

    async def list_cursor(self, *, limit: int, before_id: int | None) -> list[Warehouse]:
        stmt = self.base_select().order_by(Warehouse.id.desc()).limit(limit + 1)
        if before_id is not None:
            stmt = stmt.where(Warehouse.id < before_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_default(self) -> Warehouse | None:
        stmt = self.base_select().where(Warehouse.is_default.is_(True)).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def first_fulfillment(self) -> Warehouse | None:
        stmt = (
            self.base_select()
            .where(Warehouse.warehouse_type != "PLATFORM")
            .order_by(Warehouse.is_default.desc(), Warehouse.id.asc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def _reraise(exc: IntegrityError) -> None:
        if _unique_violation(exc, "uq_warehouse_external_code_active"):
            raise AppError("第三方仓编码已存在", code=ErrorCode.WAREHOUSE_CODE_DUPLICATED) from exc
        if _unique_violation(exc, "uq_warehouse_one_default"):
            raise AppError("已经有一个默认仓", code=ErrorCode.OPERATION_CONFLICT) from exc


class InventoryRepository(BaseRepository[Inventory]):
    model = Inventory

    async def list_by_warehouse(self, warehouse_id: int) -> list[Inventory]:
        stmt = self.base_select().where(Inventory.warehouse_id == warehouse_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_pair(self, sku_id: int, warehouse_id: int) -> Inventory | None:
        stmt = self.base_select().where(Inventory.sku_id == sku_id, Inventory.warehouse_id == warehouse_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_sku(self, sku_id: int) -> list[tuple[Inventory, Warehouse]]:
        stmt = (
            select(Inventory, Warehouse)
            .join(Warehouse, Warehouse.id == Inventory.warehouse_id)
            .where(Inventory.sku_id == sku_id, Warehouse.deleted_at.is_(None))
            .order_by(Warehouse.is_default.desc(), Warehouse.id.asc())
        )
        rows: list[tuple[Inventory, Warehouse]] = []
        for inventory, warehouse in (await self.session.execute(stmt)).all():
            rows.append((inventory, warehouse))
        return rows

    async def totals_by_sku(self, sku_ids: list[int]) -> dict[int, tuple[int, int]]:
        if not sku_ids:
            return {}
        stmt = (
            select(
                Inventory.sku_id,
                func.coalesce(func.sum(Inventory.available - Inventory.occupied), 0),
                func.coalesce(func.sum(Inventory.in_transit), 0),
            )
            .where(Inventory.sku_id.in_(sku_ids))
            .group_by(Inventory.sku_id)
        )
        found: dict[int, tuple[int, int]] = {}
        for sku_id, movable, transit in (await self.session.execute(stmt)).all():
            found[int(sku_id)] = (int(movable), int(transit))
        return found

    async def apply_change(
        self,
        inventory_id: int,
        *,
        version: int,
        available_delta: int = 0,
        occupied_delta: int = 0,
        defective_delta: int = 0,
        in_transit_delta: int = 0,
    ) -> str:
        """乐观锁更新。返回 applied、conflict、insufficient 或 missing。"""

        stmt = (
            update(Inventory)
            .where(
                Inventory.tenant_id == require_tenant_id(),
                Inventory.id == inventory_id,
                Inventory.version == version,
                Inventory.available + available_delta >= 0,
                Inventory.occupied + occupied_delta >= 0,
                Inventory.defective + defective_delta >= 0,
                Inventory.in_transit + in_transit_delta >= 0,
                Inventory.available + available_delta >= Inventory.occupied + occupied_delta,
            )
            .values(
                available=Inventory.available + available_delta,
                occupied=Inventory.occupied + occupied_delta,
                defective=Inventory.defective + defective_delta,
                in_transit=Inventory.in_transit + in_transit_delta,
                version=Inventory.version + 1,
                updated_at=func.now(),
            )
        )
        result = await self.session.execute(stmt)
        if int(getattr(result, "rowcount", 0) or 0) == 1:
            return "applied"
        current = await self._fresh(inventory_id)
        if current is None:
            return "missing"
        if current.version != version:
            return "conflict"
        return "insufficient"

    async def _fresh(self, inventory_id: int) -> Inventory | None:
        stmt = self.base_select().where(Inventory.id == inventory_id).execution_options(populate_existing=True)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_sellable(
        self,
        *,
        limit: int,
        before_id: int | None,
        sku_id: int | None,
        warehouse_id: int | None,
    ) -> list[Any]:
        """读 ``v_sellable_inventory``。原始 SQL 不会自动套租户条件，所以这里显式带上。"""

        tenant_id = get_tenant_id()
        if tenant_id is None:
            raise CrossTenantError("缺少租户上下文，拒绝读取库存")
        stmt = text(
            """
            SELECT v.id, v.sku_id, s.sku_code, v.warehouse_id, w.name AS warehouse_name,
                   w.warehouse_type, v.available, v.occupied, v.in_transit, v.defective,
                   v.safe_stock, v.version, v.sellable
            FROM v_sellable_inventory v
            JOIN sku s ON s.id = v.sku_id AND s.deleted_at IS NULL
            JOIN warehouse w ON w.id = v.warehouse_id AND w.deleted_at IS NULL
            WHERE v.tenant_id = :tenant_id
              AND (CAST(:before_id AS bigint) IS NULL OR v.id < CAST(:before_id AS bigint))
              AND (CAST(:sku_id AS bigint) IS NULL OR v.sku_id = CAST(:sku_id AS bigint))
              AND (CAST(:warehouse_id AS bigint) IS NULL OR v.warehouse_id = CAST(:warehouse_id AS bigint))
            ORDER BY v.id DESC
            LIMIT :limit
            """
        )
        rows = await self.session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "before_id": before_id,
                "sku_id": sku_id,
                "warehouse_id": warehouse_id,
                "limit": limit + 1,
            },
        )
        return list(rows.all())

    async def sales_since(self, since: datetime) -> dict[tuple[int, str], int]:
        stmt = (
            select(OrderItem.sku_id, SalesOrder.platform_code, func.coalesce(func.sum(OrderItem.quantity), 0))
            .join(SalesOrder, SalesOrder.id == OrderItem.order_id)
            .where(OrderItem.sku_id.is_not(None), OrderItem.created_at >= since)
            .group_by(OrderItem.sku_id, SalesOrder.platform_code)
        )
        found: dict[tuple[int, str], int] = {}
        for sku_id, platform_code, sold in (await self.session.execute(stmt)).all():
            if sku_id is None:
                continue
            found[(int(sku_id), str(platform_code))] = int(sold)
        return found

    async def physical_rows(self) -> list[PhysicalRow]:
        """实物 = available + occupied，不含在途。"""

        on_hand = Inventory.available + Inventory.occupied
        stmt = (
            select(
                Inventory.sku_id,
                Inventory.warehouse_id,
                on_hand,
                Sku.sku_code,
                Sku.purchase_price,
                Sku.currency,
                Warehouse.name,
            )
            .join(Sku, Sku.id == Inventory.sku_id)
            .join(Warehouse, Warehouse.id == Inventory.warehouse_id)
            .where(Sku.deleted_at.is_(None), Warehouse.deleted_at.is_(None))
        )
        rows: list[PhysicalRow] = []
        for sku_id, warehouse_id, qty, sku_code, price, currency, warehouse_name in (
            await self.session.execute(stmt)
        ).all():
            amount = None if price is None else Decimal(price)
            rows.append(
                PhysicalRow(
                    sku_id=int(sku_id),
                    warehouse_id=int(warehouse_id),
                    on_hand=int(qty),
                    sku_code=str(sku_code),
                    purchase_price=amount,
                    currency=None if currency is None else str(currency),
                    warehouse_name=str(warehouse_name),
                )
            )
        return rows


class InventoryFlowRepository(BaseRepository[InventoryFlow]):
    model = InventoryFlow

    async def list_cursor(
        self,
        *,
        limit: int,
        before_id: int | None,
        sku_id: int | None,
        warehouse_id: int | None,
        ref_type: str | None,
        ref_id: int | None,
        flow_type: str | None,
    ) -> list[InventoryFlow]:
        stmt = self.base_select().order_by(InventoryFlow.id.desc()).limit(limit + 1)
        if before_id is not None:
            stmt = stmt.where(InventoryFlow.id < before_id)
        if sku_id is not None:
            stmt = stmt.where(InventoryFlow.sku_id == sku_id)
        if warehouse_id is not None:
            stmt = stmt.where(InventoryFlow.warehouse_id == warehouse_id)
        if ref_type:
            stmt = stmt.where(InventoryFlow.ref_type == ref_type)
        if ref_id is not None:
            stmt = stmt.where(InventoryFlow.ref_id == ref_id)
        if flow_type:
            stmt = stmt.where(InventoryFlow.flow_type == flow_type)
        return list((await self.session.execute(stmt)).scalars().all())

    async def shipped_since(self, since: datetime) -> dict[tuple[int, int], int]:
        stmt = (
            select(
                InventoryFlow.sku_id,
                InventoryFlow.warehouse_id,
                func.coalesce(func.sum(InventoryFlow.quantity), 0),
            )
            .where(InventoryFlow.flow_type == "SHIP", InventoryFlow.created_at >= since)
            .group_by(InventoryFlow.sku_id, InventoryFlow.warehouse_id)
        )
        found: dict[tuple[int, int], int] = {}
        for sku_id, warehouse_id, qty in (await self.session.execute(stmt)).all():
            found[(int(sku_id), int(warehouse_id))] = int(qty)
        return found


class InventoryHoldRepository(BaseRepository[InventoryHold]):
    model = InventoryHold

    async def get_for_item(self, order_item_id: int) -> InventoryHold | None:
        stmt = self.base_select().where(InventoryHold.order_item_id == order_item_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_order(self, order_id: int) -> list[InventoryHold]:
        stmt = self.base_select().where(InventoryHold.order_id == order_id).order_by(InventoryHold.id.asc())
        return list((await self.session.execute(stmt)).scalars().all())


class PlatformSafetyStockRepository(BaseRepository[PlatformSafetyStock]):
    model = PlatformSafetyStock

    async def get_active(self, sku_id: int, platform_code: str) -> PlatformSafetyStock | None:
        stmt = self.base_select().where(
            PlatformSafetyStock.sku_id == sku_id,
            PlatformSafetyStock.platform_code == platform_code,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_cursor(self, *, limit: int, before_id: int | None) -> list[tuple[PlatformSafetyStock, str]]:
        stmt = (
            select(PlatformSafetyStock, Sku.sku_code)
            .join(Sku, Sku.id == PlatformSafetyStock.sku_id)
            .where(PlatformSafetyStock.deleted_at.is_(None), Sku.deleted_at.is_(None))
            .order_by(PlatformSafetyStock.id.desc())
            .limit(limit + 1)
        )
        if before_id is not None:
            stmt = stmt.where(PlatformSafetyStock.id < before_id)
        rows: list[tuple[PlatformSafetyStock, str]] = []
        for stock, sku_code in (await self.session.execute(stmt)).all():
            rows.append((stock, str(sku_code)))
        return rows

    async def list_active(self) -> list[tuple[PlatformSafetyStock, str]]:
        stmt = (
            select(PlatformSafetyStock, Sku.sku_code)
            .join(Sku, Sku.id == PlatformSafetyStock.sku_id)
            .where(PlatformSafetyStock.deleted_at.is_(None), Sku.deleted_at.is_(None))
            .order_by(PlatformSafetyStock.id.asc())
        )
        rows: list[tuple[PlatformSafetyStock, str]] = []
        for stock, sku_code in (await self.session.execute(stmt)).all():
            rows.append((stock, str(sku_code)))
        return rows

    async def add(self, obj: PlatformSafetyStock) -> PlatformSafetyStock:
        try:
            return await super().add(obj)
        except IntegrityError as exc:
            if _unique_violation(exc, "uq_platform_safety_stock_active"):
                raise AppError("该 SKU 在这个平台的水位已存在", code=ErrorCode.OPERATION_CONFLICT) from exc
            raise


class InventoryPushLogRepository(BaseRepository[InventoryPushLog]):
    model = InventoryPushLog

    async def latest_success_at(self, shop_id: int, sku_id: int) -> datetime | None:
        stmt = select(func.max(InventoryPushLog.created_at)).where(
            InventoryPushLog.shop_id == shop_id,
            InventoryPushLog.sku_id == sku_id,
            InventoryPushLog.status.in_(("SUCCESS", "LAGGED_ZERO")),
        )
        value = (await self.session.execute(stmt)).scalar_one_or_none()
        if value is None:
            return None
        return value

    async def list_cursor(
        self,
        *,
        limit: int,
        before_id: int | None,
        sku_id: int | None,
        shop_id: int | None,
    ) -> list[InventoryPushLog]:
        stmt = self.base_select().order_by(InventoryPushLog.id.desc()).limit(limit + 1)
        if before_id is not None:
            stmt = stmt.where(InventoryPushLog.id < before_id)
        if sku_id is not None:
            stmt = stmt.where(InventoryPushLog.sku_id == sku_id)
        if shop_id is not None:
            stmt = stmt.where(InventoryPushLog.shop_id == shop_id)
        return list((await self.session.execute(stmt)).scalars().all())


class PhysicalRow(NamedTuple):
    sku_id: int
    warehouse_id: int
    on_hand: int
    sku_code: str
    purchase_price: Decimal | None
    currency: str | None
    warehouse_name: str


class StockTransferRepository(BaseRepository[StockTransfer]):
    model = StockTransfer

    async def list_cursor(self, *, limit: int, before_id: int | None) -> list[StockTransfer]:
        stmt = self.base_select().order_by(StockTransfer.id.desc()).limit(limit + 1)
        if before_id is not None:
            stmt = stmt.where(StockTransfer.id < before_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def lines_for(self, transfer_id: int) -> list[StockTransferLine]:
        stmt = (
            select(StockTransferLine)
            .where(StockTransferLine.transfer_id == transfer_id)
            .order_by(StockTransferLine.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def transition(self, transfer_id: int, from_status: str, to_status: str, actor_id: int) -> bool:
        stmt = (
            update(StockTransfer)
            .where(
                StockTransfer.tenant_id == require_tenant_id(),
                StockTransfer.id == transfer_id,
                StockTransfer.status == from_status,
            )
            .values(status=to_status, updated_by=actor_id, updated_at=func.now())
        )
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0) == 1

    async def add_line(self, line: StockTransferLine) -> StockTransferLine:
        self.session.add(line)
        await self.session.flush()
        return line


class StockTakingRepository(BaseRepository[StockTaking]):
    model = StockTaking

    async def list_cursor(self, *, limit: int, before_id: int | None) -> list[StockTaking]:
        stmt = self.base_select().order_by(StockTaking.id.desc()).limit(limit + 1)
        if before_id is not None:
            stmt = stmt.where(StockTaking.id < before_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def lines_for(self, taking_id: int) -> list[StockTakingLine]:
        stmt = select(StockTakingLine).where(StockTakingLine.taking_id == taking_id).order_by(StockTakingLine.id.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def line_for_sku(self, taking_id: int, sku_id: int) -> StockTakingLine | None:
        stmt = select(StockTakingLine).where(
            StockTakingLine.taking_id == taking_id,
            StockTakingLine.sku_id == sku_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def transition(self, taking_id: int, from_status: str, to_status: str, actor_id: int) -> bool:
        stmt = (
            update(StockTaking)
            .where(
                StockTaking.tenant_id == require_tenant_id(),
                StockTaking.id == taking_id,
                StockTaking.status == from_status,
            )
            .values(status=to_status, updated_by=actor_id, updated_at=func.now())
        )
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0) == 1

    async def add_line(self, line: StockTakingLine) -> StockTakingLine:
        self.session.add(line)
        await self.session.flush()
        return line


__all__ = [
    "InventoryFlowRepository",
    "InventoryHoldRepository",
    "InventoryPushLogRepository",
    "InventoryRepository",
    "PhysicalRow",
    "PlatformSafetyStockRepository",
    "StockTakingRepository",
    "StockTransferRepository",
    "WarehouseRepository",
]
