"""采购域查询。SQL 只留在这一层。"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import Inventory, Warehouse
from app.models.product import Sku
from app.models.purchase import (
    FirstMileCostAllocation,
    FirstMileShipment,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReceipt,
    SkuCostPool,
    SkuSupplier,
    Supplier,
)
from app.repositories.base import BaseRepository


class SupplierRepository(BaseRepository[Supplier]):
    model = Supplier

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_cursor(self, *, limit: int, before_id: int | None) -> list[Supplier]:
        stmt = self.base_select().order_by(Supplier.id.desc()).limit(limit + 1)
        if before_id is not None:
            stmt = stmt.where(Supplier.id < before_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_key(self, key: str) -> Supplier | None:
        stmt = self.base_select().where(Supplier.idempotency_key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_name(self, name: str) -> Supplier | None:
        stmt = self.base_select().where(Supplier.name == name)
        return (await self.session.execute(stmt)).scalar_one_or_none()


class SkuSupplierRepository(BaseRepository[SkuSupplier]):
    model = SkuSupplier

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_for_supplier(self, supplier_id: int) -> list[tuple[SkuSupplier, str]]:
        stmt = (
            select(SkuSupplier, Sku.sku_code)
            .join(Sku, Sku.id == SkuSupplier.sku_id)
            .where(SkuSupplier.supplier_id == supplier_id, Sku.deleted_at.is_(None))
            .order_by(SkuSupplier.id.asc())
        )
        if hasattr(SkuSupplier, "deleted_at"):
            stmt = stmt.where(SkuSupplier.deleted_at.is_(None))
        return [(row, str(code)) for row, code in (await self.session.execute(stmt)).all()]

    async def list_for_suppliers(self, supplier_ids: list[int]) -> list[tuple[SkuSupplier, str]]:
        if not supplier_ids:
            return []
        stmt = (
            select(SkuSupplier, Sku.sku_code)
            .join(Sku, Sku.id == SkuSupplier.sku_id)
            .where(
                SkuSupplier.supplier_id.in_(supplier_ids),
                SkuSupplier.deleted_at.is_(None),
                Sku.deleted_at.is_(None),
            )
            .order_by(SkuSupplier.id.asc())
        )
        return [(row, str(code)) for row, code in (await self.session.execute(stmt)).all()]

    async def defaults_for(self, sku_ids: list[int]) -> dict[int, SkuSupplier]:
        if not sku_ids:
            return {}
        stmt = self.base_select().where(SkuSupplier.sku_id.in_(sku_ids), SkuSupplier.is_default.is_(True))
        found: dict[int, SkuSupplier] = {}
        for row in (await self.session.execute(stmt)).scalars().all():
            found[int(row.sku_id)] = row
        return found

    async def other_defaults(self, sku_id: int, supplier_id: int) -> list[SkuSupplier]:
        stmt = self.base_select().where(
            SkuSupplier.sku_id == sku_id,
            SkuSupplier.supplier_id != supplier_id,
            SkuSupplier.is_default.is_(True),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def pair(self, supplier_id: int, sku_id: int) -> SkuSupplier | None:
        return await self.get_by(supplier_id=supplier_id, sku_id=sku_id)


class PurchaseOrderRepository(BaseRepository[PurchaseOrder]):
    model = PurchaseOrder

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_cursor(self, *, limit: int, before_id: int | None) -> list[PurchaseOrder]:
        stmt = self.base_select().order_by(PurchaseOrder.id.desc()).limit(limit + 1)
        if before_id is not None:
            stmt = stmt.where(PurchaseOrder.id < before_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_key(self, key: str) -> PurchaseOrder | None:
        stmt = self.base_select().where(PurchaseOrder.idempotency_key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_key_group(self, key: str) -> list[PurchaseOrder]:
        prefix = f"{key}#"
        stmt = (
            self.base_select()
            .where(or_(PurchaseOrder.idempotency_key == key, PurchaseOrder.idempotency_key.startswith(prefix)))
            .order_by(PurchaseOrder.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_open(self, supplier_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(PurchaseOrder)
            .where(
                PurchaseOrder.supplier_id == supplier_id,
                PurchaseOrder.status.notin_(("CLOSED", "CANCELLED")),
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())


class PurchaseOrderItemRepository(BaseRepository[PurchaseOrderItem]):
    model = PurchaseOrderItem

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_for_order(self, order_id: int) -> list[tuple[PurchaseOrderItem, str]]:
        stmt = (
            select(PurchaseOrderItem, Sku.sku_code)
            .join(Sku, Sku.id == PurchaseOrderItem.sku_id)
            .where(PurchaseOrderItem.purchase_order_id == order_id)
            .order_by(PurchaseOrderItem.id.asc())
        )
        return [(row, str(code)) for row, code in (await self.session.execute(stmt)).all()]

    async def list_for_orders(self, order_ids: list[int]) -> list[tuple[PurchaseOrderItem, str]]:
        if not order_ids:
            return []
        stmt = (
            select(PurchaseOrderItem, Sku.sku_code)
            .join(Sku, Sku.id == PurchaseOrderItem.sku_id)
            .where(PurchaseOrderItem.purchase_order_id.in_(order_ids))
            .order_by(PurchaseOrderItem.id.asc())
        )
        return [(row, str(code)) for row, code in (await self.session.execute(stmt)).all()]


class PurchaseReceiptRepository(BaseRepository[PurchaseReceipt]):
    model = PurchaseReceipt

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_key(self, key: str) -> PurchaseReceipt | None:
        stmt = self.base_select().where(PurchaseReceipt.idempotency_key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()


class FirstMileShipmentRepository(BaseRepository[FirstMileShipment]):
    model = FirstMileShipment

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_cursor(self, *, limit: int, before_id: int | None) -> list[FirstMileShipment]:
        stmt = self.base_select().order_by(FirstMileShipment.id.desc()).limit(limit + 1)
        if before_id is not None:
            stmt = stmt.where(FirstMileShipment.id < before_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_by_key(self, key: str) -> FirstMileShipment | None:
        stmt = self.base_select().where(FirstMileShipment.idempotency_key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()


class FirstMileAllocationRepository(BaseRepository[FirstMileCostAllocation]):
    model = FirstMileCostAllocation

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_for_shipment(self, shipment_id: int) -> list[tuple[FirstMileCostAllocation, str]]:
        stmt = (
            select(FirstMileCostAllocation, Sku.sku_code)
            .join(Sku, Sku.id == FirstMileCostAllocation.sku_id)
            .where(FirstMileCostAllocation.shipment_id == shipment_id)
            .order_by(FirstMileCostAllocation.id.asc())
        )
        return [(row, str(code)) for row, code in (await self.session.execute(stmt)).all()]

    async def list_for_shipments(self, shipment_ids: list[int]) -> list[tuple[FirstMileCostAllocation, str]]:
        if not shipment_ids:
            return []
        stmt = (
            select(FirstMileCostAllocation, Sku.sku_code)
            .join(Sku, Sku.id == FirstMileCostAllocation.sku_id)
            .where(FirstMileCostAllocation.shipment_id.in_(shipment_ids))
            .order_by(FirstMileCostAllocation.id.asc())
        )
        return [(row, str(code)) for row, code in (await self.session.execute(stmt)).all()]


class SkuCostPoolRepository(BaseRepository[SkuCostPool]):
    model = SkuCostPool

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def latest_for_sku(self, sku_id: int) -> SkuCostPool | None:
        stmt = self.base_select().where(SkuCostPool.sku_id == sku_id).order_by(SkuCostPool.id.desc()).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_shipment(self, shipment_id: int) -> list[SkuCostPool]:
        stmt = self.base_select().where(SkuCostPool.shipment_id == shipment_id).order_by(SkuCostPool.id.asc())
        return list((await self.session.execute(stmt)).scalars().all())


class PurchaseTransitRepository:
    """在途汇总读库存余额。可售视图不含这些数量。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lines(self) -> list[tuple[Inventory, Warehouse, str]]:
        stmt = (
            select(Inventory, Warehouse, Sku.sku_code)
            .join(Warehouse, Warehouse.id == Inventory.warehouse_id)
            .join(Sku, Sku.id == Inventory.sku_id)
            .where(Inventory.in_transit > 0, Warehouse.deleted_at.is_(None), Sku.deleted_at.is_(None))
            .order_by(Warehouse.id.asc(), Sku.id.asc())
        )
        return [(inv, warehouse, str(code)) for inv, warehouse, code in (await self.session.execute(stmt)).all()]
