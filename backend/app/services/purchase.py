"""供应商、采购单、收货、头程分摊和到仓成本回写。

库存只通过库存服务改余额。到仓成本调用落地成本引擎，不另造利润口径。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    CostPoolIncompleteError,
    NotFoundError,
    ParamInvalidError,
    PurchaseStateError,
    ReceiptInvalidError,
    SupplierDefaultError,
)
from app.core.pagination import PageData, build_cursor_page, decode_cursor
from app.engines.landed_cost import LINE_LABELS, CostLine, LandedCostInput, compute_landed_cost, quantize
from app.engines.purchase import (
    AllocInput,
    PurchaseRuleError,
    allocate,
    assert_can_edit,
    assert_can_receive,
    assert_transition,
    average_formula,
    bind_first_mile,
    incomplete_landed,
    next_receipt_status,
    open_qty,
    plan_receive,
    should_move_stock,
    transit_delta_for_qty_change,
    transit_region,
    weighted_unit,
)
from app.models.enums import AuditAction
from app.models.finance import BOOK_BASIS
from app.models.inventory import Warehouse
from app.models.locale import CONTENT_MARKETS
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
from app.repositories.finance import ExchangeRateRepository
from app.repositories.hs_code import HsCodeRepository, SpuHsBindingRepository
from app.repositories.identity import AuditLogRepository
from app.repositories.inventory import WarehouseRepository
from app.repositories.product import SkuRepository
from app.repositories.purchase import (
    FirstMileAllocationRepository,
    FirstMileShipmentRepository,
    PurchaseOrderItemRepository,
    PurchaseOrderRepository,
    PurchaseReceiptRepository,
    PurchaseTransitRepository,
    SkuCostPoolRepository,
    SkuSupplierRepository,
    SupplierRepository,
)
from app.schemas.purchase import (
    AllocationView,
    CostPoolView,
    InTransitLineView,
    InTransitView,
    PurchaseLineView,
    PurchaseLineWrite,
    PurchaseOrderCreate,
    PurchaseOrderPatch,
    PurchaseOrderView,
    ReceiptCreate,
    ReceiptView,
    ReplenishmentConvert,
    ShipmentCreate,
    ShipmentView,
    SkuSupplierReplace,
    SkuSupplierView,
    SupplierPatch,
    SupplierView,
    SupplierWrite,
    shown_money,
)
from app.services.inventory import InventoryService
from app.services.landed_cost import LandedCostService

_UNIT_FIELDS = {
    "PURCHASE": "purchase_unit",
    "FIRST_MILE": "first_mile_unit",
    "DUTY": "duty_unit",
    "IMPORT_TAX": "import_tax_unit",
    "BROKERAGE": "brokerage_unit",
    "STORAGE": "storage_unit",
    "FX_RESERVE": "fx_reserve_unit",
}


def _clean_key(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if any(char in text for char in "%_#"):
        raise ParamInvalidError("幂等键不能包含 %、_ 或 #")
    return text


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


def _raise_rule(exc: PurchaseRuleError) -> None:
    if exc.code == "state":
        raise PurchaseStateError(str(exc)) from exc
    raise ReceiptInvalidError(str(exc)) from exc


def _extended(quantity: int, unit_price: Decimal) -> Decimal:
    return quantize(unit_price * Decimal(quantity))


class PurchaseService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.suppliers = SupplierRepository(session)
        self.links = SkuSupplierRepository(session)
        self.orders = PurchaseOrderRepository(session)
        self.items = PurchaseOrderItemRepository(session)
        self.receipts = PurchaseReceiptRepository(session)
        self.shipments = FirstMileShipmentRepository(session)
        self.allocations = FirstMileAllocationRepository(session)
        self.pools = SkuCostPoolRepository(session)
        self.transit = PurchaseTransitRepository(session)
        self.skus = SkuRepository(session)
        self.warehouses = WarehouseRepository(session)
        self.rates = ExchangeRateRepository(session)
        self.hs_bindings = SpuHsBindingRepository(session)
        self.hs_codes = HsCodeRepository(session)
        self.audit = AuditLogRepository(session)

    async def list_suppliers(self, *, limit: int, cursor: str | None) -> PageData[SupplierView]:
        rows = await self.suppliers.list_cursor(limit=limit, before_id=_cursor_id(cursor))
        page = build_cursor_page(rows, limit)
        pairs = await self.links.list_for_suppliers([row.id for row in page.items])
        grouped: dict[int, list[SkuSupplierView]] = {}
        for link, code in pairs:
            grouped.setdefault(int(link.supplier_id), []).append(
                SkuSupplierView(sku_id=int(link.sku_id), sku_code=code, is_default=link.is_default)
            )
        views = [_supplier_view(row, grouped.get(row.id, [])) for row in page.items]
        return PageData(items=views, page_info=page.page_info)

    async def create_supplier(
        self,
        payload: SupplierWrite,
        *,
        tenant_id: int,
        actor_id: int,
        idempotency_key: str | None,
    ) -> SupplierView:
        key = _clean_key(idempotency_key)
        if key:
            existing = await self.suppliers.get_by_key(key)
            if existing is not None:
                return await self._supplier(existing.id)
        if await self.suppliers.get_by_name(payload.name) is not None:
            raise ConflictError("供应商名称已存在")
        row = Supplier(
            tenant_id=tenant_id,
            name=payload.name,
            contact=payload.contact,
            settlement_type=payload.settlement_type,
            credit_days=payload.credit_days,
            rating=payload.rating,
            idempotency_key=key,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.suppliers.add(row)
        await self._audit(tenant_id, actor_id, "supplier", row.id, None, {"name": row.name})
        return _supplier_view(row, [])

    async def update_supplier(
        self,
        supplier_id: int,
        payload: SupplierPatch,
        *,
        tenant_id: int,
        actor_id: int,
    ) -> SupplierView:
        row = await self.suppliers.get_or_404(supplier_id)
        before = {"name": row.name, "settlement_type": row.settlement_type, "rating": row.rating}
        fields = payload.model_fields_set
        if "name" in fields and payload.name:
            other = await self.suppliers.get_by_name(payload.name)
            if other is not None and other.id != row.id:
                raise ConflictError("供应商名称已存在")
            row.name = payload.name
        if "contact" in fields and payload.contact is not None:
            row.contact = payload.contact
        if "settlement_type" in fields and payload.settlement_type:
            row.settlement_type = payload.settlement_type
        if "credit_days" in fields and payload.credit_days is not None:
            row.credit_days = payload.credit_days
        if "rating" in fields:
            row.rating = payload.rating
        row.updated_by = actor_id
        await self.session.flush()
        await self._audit(tenant_id, actor_id, "supplier", row.id, before, {"name": row.name})
        return await self._supplier(row.id)

    async def delete_supplier(self, supplier_id: int, *, tenant_id: int, actor_id: int) -> SupplierView:
        row = await self.suppliers.get_or_404(supplier_id)
        if await self.orders.count_open(supplier_id):
            raise ConflictError("供应商还有未关闭的采购单")
        view = await self._supplier(row.id)
        for link, _code in await self.links.list_for_supplier(supplier_id):
            link.updated_by = actor_id
            await self.links.soft_delete(link)
        row.updated_by = actor_id
        await self.suppliers.soft_delete(row)
        await self._audit(tenant_id, actor_id, "supplier", supplier_id, {"name": row.name}, {"deleted": "true"})
        return view

    async def replace_links(
        self,
        supplier_id: int,
        payload: SkuSupplierReplace,
        *,
        tenant_id: int,
        actor_id: int,
    ) -> SupplierView:
        await self.suppliers.get_or_404(supplier_id)
        sku_ids = [link.sku_id for link in payload.links]
        if len(sku_ids) != len(set(sku_ids)):
            raise ParamInvalidError("同一供应商不能重复绑定 SKU")
        for sku_id in sku_ids:
            await self.skus.get_or_404(sku_id)
        current = {int(row.sku_id): row for row, _code in await self.links.list_for_supplier(supplier_id)}
        requested = {link.sku_id: link for link in payload.links}
        for sku_id, row in current.items():
            if sku_id not in requested:
                row.updated_by = actor_id
                await self.links.soft_delete(row)
        try:
            for link in payload.links:
                if link.is_default:
                    for other in await self.links.other_defaults(link.sku_id, supplier_id):
                        other.is_default = False
                        other.updated_by = actor_id
                    await self.session.flush()
                found = current.get(link.sku_id)
                if found is None:
                    await self.links.add(
                        SkuSupplier(
                            tenant_id=tenant_id,
                            supplier_id=supplier_id,
                            sku_id=link.sku_id,
                            is_default=link.is_default,
                            created_by=actor_id,
                            updated_by=actor_id,
                        )
                    )
                else:
                    found.is_default = link.is_default
                    found.updated_by = actor_id
                    await self.session.flush()
        except IntegrityError as exc:
            raise SupplierDefaultError() from exc
        await self._audit(
            tenant_id, actor_id, "sku_supplier", supplier_id, None, {"sku_ids": [str(item) for item in sku_ids]}
        )
        return await self._supplier(supplier_id)

    async def list_orders(
        self,
        *,
        limit: int,
        cursor: str | None,
        visible: bool,
    ) -> PageData[PurchaseOrderView]:
        rows = await self.orders.list_cursor(limit=limit, before_id=_cursor_id(cursor))
        page = build_cursor_page(rows, limit)
        views = [await self._order_view(row, visible=visible) for row in page.items]
        return PageData(items=views, page_info=page.page_info)

    async def create_order(
        self,
        payload: PurchaseOrderCreate,
        *,
        tenant_id: int,
        actor_id: int,
        idempotency_key: str | None,
        visible: bool,
    ) -> PurchaseOrderView:
        key = _clean_key(idempotency_key)
        if key:
            existing = await self.orders.get_by_key(key)
            if existing is not None:
                return await self._order_view(existing, visible=visible)
        order = await self._insert_order(payload, tenant_id=tenant_id, actor_id=actor_id, idempotency_key=key)
        return await self._order_view(order, visible=visible)

    async def update_order(
        self,
        order_id: int,
        payload: PurchaseOrderPatch,
        *,
        tenant_id: int,
        actor_id: int,
        visible: bool,
    ) -> PurchaseOrderView:
        order = await self.orders.get_or_404(order_id)
        try:
            assert_can_edit(order.status)
        except PurchaseRuleError as exc:
            _raise_rule(exc)
        before = {"status": order.status, "total_amount": str(order.total_amount)}
        if payload.note is not None:
            order.note = payload.note
        if "expected_on" in payload.model_fields_set:
            order.expected_on = payload.expected_on
        if payload.lines is not None:
            await self._apply_line_edits(order, payload, tenant_id=tenant_id, actor_id=actor_id)
        order.updated_by = actor_id
        await self.session.flush()
        await self._audit(
            tenant_id, actor_id, "purchase_order", order.id, before, {"total_amount": str(order.total_amount)}
        )
        return await self._order_view(order, visible=visible)

    async def transition(
        self,
        order_id: int,
        action: str,
        *,
        tenant_id: int,
        actor_id: int,
        visible: bool,
    ) -> PurchaseOrderView:
        order = await self.orders.get_or_404(order_id)
        item_rows = await self.items.list_for_order(order.id)
        received = sum(row.received_qty for row, _code in item_rows)
        previous = order.status
        if action == "close" and any(
            open_qty(row.quantity, row.received_qty, row.short_qty) for row, _code in item_rows
        ):
            raise PurchaseStateError("还有未结清的数量，请先短收或继续收货")
        if action == "submit" and not item_rows:
            raise ParamInvalidError("采购单至少要有一行")
        try:
            order.status = assert_transition(previous, action, received=received)
        except PurchaseRuleError as exc:
            _raise_rule(exc)
        order.updated_by = actor_id
        if action == "approve":
            for row, _code in item_rows:
                await self._move(
                    sku_id=row.sku_id,
                    warehouse_id=order.warehouse_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    in_transit_delta=row.quantity,
                    ref_type="PURCHASE_ORDER",
                    ref_id=order.id,
                )
        if action == "cancel" and previous == "APPROVED":
            for row, _code in item_rows:
                release = open_qty(row.quantity, row.received_qty, row.short_qty)
                if release:
                    await self._move(
                        sku_id=row.sku_id,
                        warehouse_id=order.warehouse_id,
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        in_transit_delta=-release,
                        ref_type="PURCHASE_ORDER",
                        ref_id=order.id,
                    )
        await self.session.flush()
        await self._audit(
            tenant_id, actor_id, "purchase_order", order.id, None, {"status": order.status, "action": action}
        )
        return await self._order_view(order, visible=visible)

    async def receive(
        self,
        order_id: int,
        payload: ReceiptCreate,
        *,
        tenant_id: int,
        actor_id: int,
        idempotency_key: str | None,
        visible: bool,
    ) -> ReceiptView:
        key = _clean_key(idempotency_key)
        if key:
            existing = await self.receipts.get_by_key(key)
            if existing is not None:
                order = await self.orders.get_or_404(existing.purchase_order_id)
                return ReceiptView(
                    id=existing.id,
                    purchase_order_id=order.id,
                    disposition=existing.disposition,
                    note=existing.note,
                    order=await self._order_view(order, visible=visible),
                )
        order = await self.orders.get_or_404(order_id)
        try:
            assert_can_receive(order.status)
        except PurchaseRuleError as exc:
            _raise_rule(exc)
        item_rows = await self.items.list_for_order(order.id)
        by_id = {int(row.id): row for row, _code in item_rows}
        if len(payload.lines) != len({line.item_id for line in payload.lines}):
            raise ReceiptInvalidError("收货明细重复")
        recorded: list[dict[str, int | str]] = []
        try:
            for line in payload.lines:
                item = by_id.get(line.item_id)
                if item is None:
                    raise NotFoundError()
                plan = plan_receive(
                    ordered=item.quantity,
                    received=item.received_qty,
                    short=item.short_qty,
                    quantity=line.quantity,
                    disposition=payload.disposition,
                    allow_over=payload.allow_over,
                )
                item.received_qty = plan.new_received
                item.short_qty = plan.new_short
                item.updated_by = actor_id
                await self._move(
                    sku_id=item.sku_id,
                    warehouse_id=order.warehouse_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    available_delta=plan.receive_qty,
                    in_transit_delta=-plan.transit_release,
                    ref_type="PURCHASE_SHORT" if payload.disposition == "SHORT" else "PURCHASE_RECEIPT",
                    ref_id=order.id,
                )
                recorded.append(
                    {
                        "item_id": str(item.id),
                        "sku_id": str(item.sku_id),
                        "receive_qty": plan.receive_qty,
                        "transit_release": plan.transit_release,
                    }
                )
        except PurchaseRuleError as exc:
            _raise_rule(exc)
        fresh = await self.items.list_for_order(order.id)
        order.status = next_receipt_status([(row.quantity, row.received_qty, row.short_qty) for row, _code in fresh])
        order.updated_by = actor_id
        receipt = PurchaseReceipt(
            tenant_id=tenant_id,
            purchase_order_id=order.id,
            disposition=payload.disposition,
            lines=recorded,
            note=payload.note,
            idempotency_key=key,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.receipts.add(receipt)
        await self._audit(
            tenant_id,
            actor_id,
            "purchase_receipt",
            receipt.id,
            None,
            {"order_id": str(order.id), "disposition": payload.disposition},
        )
        return ReceiptView(
            id=receipt.id,
            purchase_order_id=order.id,
            disposition=receipt.disposition,
            note=receipt.note,
            order=await self._order_view(order, visible=visible),
        )

    async def in_transit(self) -> InTransitView:
        domestic = 0
        overseas = 0
        lines: list[InTransitLineView] = []
        for inventory, warehouse, sku_code in await self.transit.lines():
            try:
                region = transit_region(warehouse.warehouse_type)
            except PurchaseRuleError:
                continue
            quantity = int(inventory.in_transit)
            if region == "DOMESTIC":
                domestic += quantity
            else:
                overseas += quantity
            lines.append(
                InTransitLineView(
                    region=region,
                    warehouse_id=int(warehouse.id),
                    warehouse_name=warehouse.name,
                    warehouse_type=warehouse.warehouse_type,
                    sku_id=int(inventory.sku_id),
                    sku_code=sku_code,
                    quantity=quantity,
                )
            )
        return InTransitView(domestic_qty=domestic, overseas_qty=overseas, lines=lines)

    async def list_shipments(
        self,
        *,
        limit: int,
        cursor: str | None,
        visible: bool,
    ) -> PageData[ShipmentView]:
        rows = await self.shipments.list_cursor(limit=limit, before_id=_cursor_id(cursor))
        page = build_cursor_page(rows, limit)
        views = [await self._shipment_view(row, visible=visible) for row in page.items]
        return PageData(items=views, page_info=page.page_info)

    async def create_shipment(
        self,
        payload: ShipmentCreate,
        *,
        tenant_id: int,
        actor_id: int,
        idempotency_key: str | None,
        visible: bool,
    ) -> ShipmentView:
        key = _clean_key(idempotency_key)
        if key:
            existing = await self.shipments.get_by_key(key)
            if existing is not None:
                return await self._shipment_view(existing, visible=visible)
        await self._check_shipment_refs(payload)
        if payload.destination_market not in CONTENT_MARKETS:
            raise ParamInvalidError("目的国不支持")
        sku_ids = [line.sku_id for line in payload.lines]
        if len(sku_ids) != len(set(sku_ids)):
            raise ParamInvalidError("头程明细不能重复 SKU")
        for sku_id in sku_ids:
            await self.skus.get_or_404(sku_id)
        row = FirstMileShipment(
            tenant_id=tenant_id,
            purchase_order_id=payload.purchase_order_id,
            from_warehouse_id=payload.from_warehouse_id,
            to_warehouse_id=payload.to_warehouse_id,
            forwarder=payload.forwarder,
            channel=payload.channel,
            container_no=payload.container_no,
            destination_market=payload.destination_market,
            cost_total=payload.cost_total,
            currency=payload.currency,
            alloc_method=payload.alloc_method,
            storage_days=payload.storage_days,
            status="DRAFT",
            lines=[{"sku_id": line.sku_id, "quantity": line.quantity} for line in payload.lines],
            idempotency_key=key,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.shipments.add(row)
        await self._audit(tenant_id, actor_id, "first_mile_shipment", row.id, None, {"status": "DRAFT"})
        return await self._shipment_view(row, visible=visible)

    async def post_shipment(
        self,
        shipment_id: int,
        *,
        tenant_id: int,
        actor_id: int,
        visible: bool,
    ) -> ShipmentView:
        shipment = await self.shipments.get_or_404(shipment_id)
        if shipment.status == "POSTED":
            return await self._shipment_view(shipment, visible=visible)
        po_type = await self._po_warehouse_type(shipment.purchase_order_id)
        outputs = await self._allocation_inputs(shipment)
        try:
            allocated = allocate(shipment.alloc_method, shipment.cost_total, str(shipment.currency).strip(), outputs)
        except PurchaseRuleError as exc:
            _raise_rule(exc)
        if should_move_stock(
            from_id=shipment.from_warehouse_id,
            to_id=shipment.to_warehouse_id,
            po_warehouse_type=po_type,
        ):
            assert shipment.from_warehouse_id is not None
            assert shipment.to_warehouse_id is not None
            for line in allocated:
                await self._move(
                    sku_id=line.sku_id,
                    warehouse_id=shipment.from_warehouse_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    available_delta=-line.quantity,
                    ref_type="FIRST_MILE",
                    ref_id=shipment.id,
                )
                await self._move(
                    sku_id=line.sku_id,
                    warehouse_id=shipment.to_warehouse_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    in_transit_delta=line.quantity,
                    ref_type="FIRST_MILE",
                    ref_id=shipment.id,
                )
        for line in allocated:
            await self.allocations.add(
                FirstMileCostAllocation(
                    tenant_id=tenant_id,
                    shipment_id=shipment.id,
                    sku_id=line.sku_id,
                    quantity=line.quantity,
                    allocated_cost=line.allocated_cost,
                    currency=str(shipment.currency).strip(),
                    method=shipment.alloc_method,
                    formula=line.formula,
                    source=line.source,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            await self._write_pool(
                shipment, line.sku_id, line.quantity, line.unit_cost, line.formula, tenant_id, actor_id
            )
        shipment.status = "POSTED"
        shipment.updated_by = actor_id
        await self.session.flush()
        await self._audit(
            tenant_id, actor_id, "first_mile_shipment", shipment.id, {"status": "DRAFT"}, {"status": "POSTED"}
        )
        return await self._shipment_view(shipment, visible=visible)

    async def from_replenishment(
        self,
        payload: ReplenishmentConvert,
        *,
        tenant_id: int,
        actor_id: int,
        idempotency_key: str | None,
        visible: bool,
    ) -> list[PurchaseOrderView]:
        key = _clean_key(idempotency_key)
        if key:
            existing = await self.orders.list_by_key_group(key)
            if existing:
                return [await self._order_view(row, visible=visible) for row in existing]
        suggestions = await InventoryService(self.session).replenishments(window_days=payload.window_days)
        chosen = [row for row in suggestions if row.sku_id in set(payload.sku_ids) and row.suggested_qty > 0]
        if not chosen:
            raise ParamInvalidError("没有可转成采购单的补货建议")
        grouped: dict[int, list[tuple[int, int, Decimal]]] = {}
        defaults = await self.links.defaults_for([row.sku_id for row in chosen])
        for row in chosen:
            supplier_id = payload.supplier_id or (defaults[row.sku_id].supplier_id if row.sku_id in defaults else None)
            if supplier_id is None:
                raise ParamInvalidError("SKU 没有默认供应商")
            sku = await self.skus.get_or_404(row.sku_id)
            price = _suggestion_price(payload, sku)
            grouped.setdefault(int(supplier_id), []).append((row.sku_id, row.suggested_qty, price))
        created: list[PurchaseOrder] = []
        for supplier_id, lines in grouped.items():
            suffix = key if len(grouped) == 1 else None
            if key and len(grouped) > 1:
                suffix = f"{key}#{supplier_id}"
            order = await self._insert_order(
                PurchaseOrderCreate(
                    supplier_id=supplier_id,
                    warehouse_id=payload.warehouse_id,
                    currency=payload.currency,
                    note="由补货建议生成",
                    lines=[
                        PurchaseLineWrite(sku_id=sku_id, quantity=quantity, unit_price=price, tax_included=False)
                        for sku_id, quantity, price in lines
                    ],
                ),
                tenant_id=tenant_id,
                actor_id=actor_id,
                idempotency_key=suffix,
            )
            created.append(order)
        return [await self._order_view(row, visible=visible) for row in created]

    async def _insert_order(
        self,
        payload: PurchaseOrderCreate,
        *,
        tenant_id: int,
        actor_id: int,
        idempotency_key: str | None,
    ) -> PurchaseOrder:
        await self.suppliers.get_or_404(payload.supplier_id)
        warehouse = await self._destination(payload.warehouse_id)
        sku_ids = [line.sku_id for line in payload.lines]
        if len(sku_ids) != len(set(sku_ids)):
            raise ParamInvalidError("采购明细不能重复 SKU")
        total = Decimal("0")
        for line in payload.lines:
            await self.skus.get_or_404(line.sku_id)
            total += _extended(line.quantity, line.unit_price)
        order = PurchaseOrder(
            tenant_id=tenant_id,
            supplier_id=payload.supplier_id,
            warehouse_id=warehouse.id,
            status="DRAFT",
            currency=payload.currency,
            total_amount=quantize(total),
            expected_on=payload.expected_on,
            note=payload.note,
            idempotency_key=idempotency_key,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.orders.add(order)
        for line in payload.lines:
            await self.items.add(
                PurchaseOrderItem(
                    tenant_id=tenant_id,
                    purchase_order_id=order.id,
                    sku_id=line.sku_id,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    currency=payload.currency,
                    tax_included=line.tax_included,
                    expected_on=line.expected_on,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
        await self._audit(tenant_id, actor_id, "purchase_order", order.id, None, {"status": "DRAFT"})
        return order

    async def _apply_line_edits(
        self,
        order: PurchaseOrder,
        payload: PurchaseOrderPatch,
        *,
        tenant_id: int,
        actor_id: int,
    ) -> None:
        assert payload.lines is not None
        current = {int(row.sku_id): row for row, _code in await self.items.list_for_order(order.id)}
        seen: set[int] = set()
        for line in payload.lines:
            if line.sku_id in seen:
                raise ParamInvalidError("采购明细不能重复 SKU")
            seen.add(line.sku_id)
            item = current.get(line.sku_id)
            if item is None:
                await self.skus.get_or_404(line.sku_id)
                if order.status in {"APPROVED", "PARTIAL"}:
                    await self._move(
                        sku_id=line.sku_id,
                        warehouse_id=order.warehouse_id,
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        in_transit_delta=line.quantity,
                        ref_type="PURCHASE_ORDER",
                        ref_id=order.id,
                    )
                await self.items.add(
                    PurchaseOrderItem(
                        tenant_id=tenant_id,
                        purchase_order_id=order.id,
                        sku_id=line.sku_id,
                        quantity=line.quantity,
                        unit_price=line.unit_price,
                        currency=str(order.currency).strip(),
                        tax_included=line.tax_included,
                        expected_on=line.expected_on,
                        created_by=actor_id,
                        updated_by=actor_id,
                    )
                )
                continue
            try:
                delta = transit_delta_for_qty_change(
                    order.status,
                    item.quantity,
                    item.received_qty,
                    item.short_qty,
                    line.quantity,
                )
            except PurchaseRuleError as exc:
                _raise_rule(exc)
                raise
            if delta:
                await self._move(
                    sku_id=item.sku_id,
                    warehouse_id=order.warehouse_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    in_transit_delta=delta,
                    ref_type="PURCHASE_ORDER",
                    ref_id=order.id,
                )
            item.quantity = line.quantity
            item.unit_price = line.unit_price
            item.tax_included = line.tax_included
            item.expected_on = line.expected_on
            item.updated_by = actor_id
        fresh = await self.items.list_for_order(order.id)
        order.total_amount = quantize(
            sum((_extended(row.quantity, row.unit_price) for row, _code in fresh), Decimal("0"))
        )

    async def _write_pool(
        self,
        shipment: FirstMileShipment,
        sku_id: int,
        quantity: int,
        unit_first_mile: Decimal,
        formula: str,
        tenant_id: int,
        actor_id: int,
    ) -> None:
        sku = await self.skus.get_or_404(sku_id)
        currency = str(shipment.currency).strip()
        price, price_currency = await self._purchase_price(shipment, sku)
        today = datetime.now(UTC).date()
        fx_rate, fx_source = await self._fx(price_currency, currency, today)
        declared = (
            price
            if price_currency == currency
            else (None if fx_rate is None or price is None else quantize(price / fx_rate))
        )
        taxes, fees, disabled = await LandedCostService(self.session).load_rules(
            shipment.destination_market,
            shipment.channel,
            today,
        )
        result = compute_landed_cost(
            LandedCostInput(
                market=shipment.destination_market,
                selling_currency=currency,
                channel=shipment.channel,
                first_mile_method="WEIGHT",
                purchase_amount=price,
                purchase_currency=price_currency,
                fx_rate=fx_rate,
                fx_source=fx_source,
                weight_g=Decimal("1"),
                volume_cm3=sku.length_cm * sku.width_cm * sku.height_cm,
                hs_code=await self._hs(sku.spu_id, shipment.destination_market),
                declared_value=declared,
                declared_currency=None if declared is None else currency,
                shipment_cost=unit_first_mile,
                shipment_currency=currency,
                shipment_weight_g=Decimal("1"),
                storage_days=shipment.storage_days,
            ),
            taxes,
            fees,
            disabled=disabled - {"FIRST_MILE"},
        )
        bound = bind_first_mile(result, formula, "头程物流单")
        missing = incomplete_landed(bound)
        if missing or bound.landed_cost is None:
            raise CostPoolIncompleteError(data={"lines": missing})
        previous = await self.pools.latest_for_sku(sku_id)
        if previous is not None and str(previous.currency).strip() != currency:
            previous = None
        old_qty = 0 if previous is None else int(previous.quantity)
        amounts = _averaged(previous, bound.lines, old_qty, quantity)
        landed = weighted_unit(
            old_qty, Decimal("0") if previous is None else previous.landed_unit, quantity, bound.landed_cost
        )
        row = SkuCostPool(
            tenant_id=tenant_id,
            sku_id=sku_id,
            shipment_id=shipment.id,
            purchase_order_id=shipment.purchase_order_id,
            quantity=old_qty + quantity,
            currency=currency,
            purchase_unit=amounts["PURCHASE"],
            first_mile_unit=amounts["FIRST_MILE"],
            duty_unit=amounts["DUTY"],
            import_tax_unit=amounts["IMPORT_TAX"],
            brokerage_unit=amounts["BROKERAGE"],
            storage_unit=amounts["STORAGE"],
            fx_reserve_unit=amounts["FX_RESERVE"],
            landed_unit=landed,
            lines=_pool_lines(
                bound.lines,
                amounts,
                previous,
                old_qty,
                quantity,
                currency,
                bound.landed_cost,
                landed,
            ),
            source=f"first_mile_shipment#{shipment.id}",
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.pools.add(row)
        await self._audit(
            tenant_id, actor_id, "sku_cost_pool", row.id, None, {"sku_id": str(sku_id), "source": row.source}
        )

    async def _purchase_price(self, shipment: FirstMileShipment, sku: Sku) -> tuple[Decimal | None, str | None]:
        if shipment.purchase_order_id is not None:
            for item, _code in await self.items.list_for_order(shipment.purchase_order_id):
                if int(item.sku_id) == int(sku.id):
                    return item.unit_price, str(item.currency).strip()
        if sku.purchase_price is None or sku.currency is None:
            return None, None
        return sku.purchase_price, str(sku.currency).strip()

    async def _fx(self, purchase_currency: str | None, book_currency: str, on: date) -> tuple[Decimal | None, str]:
        if purchase_currency is None or purchase_currency == book_currency:
            return None, ""
        row = await self.rates.latest(book_currency, purchase_currency, BOOK_BASIS, on)
        if row is None:
            return None, ""
        return row.rate, row.source

    async def _hs(self, spu_id: int, market: str) -> str:
        binding = await self.hs_bindings.get_for_market(spu_id, market)
        if binding is None:
            return ""
        row = await self.hs_codes.get(binding.hs_code_id)
        return "" if row is None else row.code

    async def _destination(self, warehouse_id: int) -> Warehouse:
        warehouse = await self.warehouses.get_or_404(warehouse_id)
        try:
            transit_region(warehouse.warehouse_type)
        except PurchaseRuleError as exc:
            raise ParamInvalidError(str(exc)) from exc
        return warehouse

    async def _check_shipment_refs(self, payload: ShipmentCreate) -> None:
        if payload.purchase_order_id is not None:
            await self.orders.get_or_404(payload.purchase_order_id)
        for warehouse_id in (payload.from_warehouse_id, payload.to_warehouse_id):
            if warehouse_id is not None:
                await self._destination(warehouse_id)

    async def _po_warehouse_type(self, order_id: int | None) -> str | None:
        if order_id is None:
            return None
        order = await self.orders.get_or_404(order_id)
        warehouse = await self.warehouses.get_or_404(order.warehouse_id)
        return warehouse.warehouse_type

    async def _allocation_inputs(self, shipment: FirstMileShipment) -> list[AllocInput]:
        raw_lines = shipment.lines if isinstance(shipment.lines, list) else []
        currency = str(shipment.currency).strip()
        today = datetime.now(UTC).date()
        inputs: list[AllocInput] = []
        for raw in raw_lines:
            sku = await self.skus.get_or_404(int(raw["sku_id"]))
            price, price_currency = await self._purchase_price(shipment, sku)
            fx_rate, _source = await self._fx(price_currency, currency, today)
            if price is None:
                unit_value = Decimal("0")
            elif price_currency == currency:
                unit_value = price
            elif fx_rate is None:
                unit_value = Decimal("0")
            else:
                unit_value = quantize(price / fx_rate)
            inputs.append(
                AllocInput(
                    sku_id=int(sku.id),
                    quantity=int(raw["quantity"]),
                    weight_g=sku.weight_g,
                    volume_cm3=sku.length_cm * sku.width_cm * sku.height_cm,
                    unit_value=unit_value,
                )
            )
        return inputs

    async def _move(
        self,
        *,
        sku_id: int,
        warehouse_id: int,
        tenant_id: int,
        actor_id: int,
        ref_type: str,
        ref_id: int,
        available_delta: int = 0,
        in_transit_delta: int = 0,
    ) -> None:
        await InventoryService(self.session).post_stock(
            sku_id=sku_id,
            warehouse_id=warehouse_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            available_delta=available_delta,
            in_transit_delta=in_transit_delta,
            ref_type=ref_type,
            ref_id=ref_id,
        )

    async def _supplier(self, supplier_id: int) -> SupplierView:
        row = await self.suppliers.get_or_404(supplier_id)
        skus = [
            SkuSupplierView(sku_id=int(link.sku_id), sku_code=code, is_default=link.is_default)
            for link, code in await self.links.list_for_supplier(supplier_id)
        ]
        return _supplier_view(row, skus)

    async def _order_view(self, order: PurchaseOrder, *, visible: bool) -> PurchaseOrderView:
        supplier = await self.suppliers.get_or_404(order.supplier_id)
        warehouse = await self.warehouses.get_or_404(order.warehouse_id)
        lines = []
        for item, code in await self.items.list_for_order(order.id):
            lines.append(
                PurchaseLineView(
                    id=item.id,
                    sku_id=item.sku_id,
                    sku_code=code,
                    quantity=item.quantity,
                    received_qty=item.received_qty,
                    short_qty=item.short_qty,
                    open_qty=open_qty(item.quantity, item.received_qty, item.short_qty),
                    unit_price=shown_money(item.unit_price, visible=visible),
                    currency=str(item.currency).strip() if visible else None,
                    tax_included=item.tax_included,
                    expected_on=item.expected_on,
                )
            )
        return PurchaseOrderView(
            id=order.id,
            supplier_id=order.supplier_id,
            supplier_name=supplier.name,
            warehouse_id=order.warehouse_id,
            warehouse_name=warehouse.name,
            warehouse_type=warehouse.warehouse_type,
            status=order.status,
            currency=str(order.currency).strip() if visible else None,
            total_amount=shown_money(order.total_amount, visible=visible),
            expected_on=order.expected_on,
            note=order.note,
            lines=lines,
        )

    async def _shipment_view(self, shipment: FirstMileShipment, *, visible: bool) -> ShipmentView:
        allocations = [
            AllocationView(
                id=row.id,
                sku_id=row.sku_id,
                sku_code=code,
                quantity=row.quantity,
                allocated_cost=shown_money(row.allocated_cost, visible=visible),
                currency=str(row.currency).strip() if visible else None,
                method=row.method,
                formula=row.formula if visible else "",
                source=row.source if visible else "",
            )
            for row, code in await self.allocations.list_for_shipment(shipment.id)
        ]
        pools = [_pool_view(row, visible=visible) for row in await self.pools.list_for_shipment(shipment.id)]
        return ShipmentView(
            id=shipment.id,
            purchase_order_id=shipment.purchase_order_id,
            from_warehouse_id=shipment.from_warehouse_id,
            to_warehouse_id=shipment.to_warehouse_id,
            forwarder=shipment.forwarder,
            channel=shipment.channel,
            container_no=shipment.container_no,
            destination_market=shipment.destination_market,
            cost_total=shown_money(shipment.cost_total, visible=visible),
            currency=str(shipment.currency).strip() if visible else None,
            alloc_method=shipment.alloc_method,
            storage_days=shipment.storage_days,
            status=shipment.status,
            allocations=allocations,
            pools=pools,
        )

    async def _audit(
        self,
        tenant_id: int,
        actor_id: int,
        resource: str,
        resource_id: int,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.PURCHASE_CHANGE,
            resource=resource,
            resource_id=resource_id,
            before=before,
            after=after,
        )


def _supplier_view(row: Supplier, skus: list[SkuSupplierView]) -> SupplierView:
    return SupplierView(
        id=row.id,
        name=row.name,
        contact=row.contact,
        settlement_type=row.settlement_type,
        credit_days=row.credit_days,
        rating=row.rating,
        skus=skus,
    )


def _suggestion_price(payload: ReplenishmentConvert, sku: Sku) -> Decimal:
    if payload.unit_price is not None:
        return payload.unit_price
    if sku.purchase_price is not None and sku.currency is not None and str(sku.currency).strip() == payload.currency:
        return sku.purchase_price
    raise ParamInvalidError("补货转采购单时需要采购价")


def _averaged(
    previous: SkuCostPool | None,
    lines: tuple[CostLine, ...],
    old_qty: int,
    new_qty: int,
) -> dict[str, Decimal]:
    by_code = {line.code: line for line in lines}
    amounts: dict[str, Decimal] = {}
    for code in _UNIT_FIELDS:
        arrival = by_code[code].amount or Decimal("0")
        old_unit = Decimal("0") if previous is None else getattr(previous, _UNIT_FIELDS[code])
        amounts[code] = weighted_unit(old_qty, old_unit, new_qty, arrival)
    return amounts


def _pool_lines(
    arrival: tuple[CostLine, ...],
    amounts: dict[str, Decimal],
    previous: SkuCostPool | None,
    old_qty: int,
    new_qty: int,
    currency: str,
    arrival_landed: Decimal,
    landed: Decimal,
) -> list[dict[str, str | bool | None]]:
    by_code = {line.code: line for line in arrival}
    rows: list[dict[str, str | bool | None]] = []
    for code, amount in amounts.items():
        line = by_code[code]
        old_unit = Decimal("0") if previous is None else getattr(previous, _UNIT_FIELDS[code])
        if old_qty <= 0:
            formula = line.formula
            source = line.source
        else:
            formula = average_formula(
                LINE_LABELS[code], old_qty, old_unit, new_qty, line.amount or Decimal("0"), amount, currency
            )
            source = f"sku_cost_pool#{previous.id}；{line.source}" if previous is not None else line.source
        rows.append(
            {
                "code": code,
                "label": LINE_LABELS[code],
                "amount": shown_money(amount, visible=True),
                "currency": currency,
                "formula": formula,
                "source": source,
                "complete": True,
            }
        )
    rows.append(
        {
            "code": "LANDED",
            "label": "到仓成本",
            "amount": shown_money(landed, visible=True),
            "currency": currency,
            "formula": average_formula(
                "到仓成本",
                old_qty,
                Decimal("0") if previous is None else previous.landed_unit,
                new_qty,
                arrival_landed,
                landed,
                currency,
            ),
            "source": "采购 + 头程 + 关税 + 进口环节税 + 报关 + 仓储 + 汇兑预备金",
            "complete": True,
        }
    )
    return rows


def _pool_view(row: SkuCostPool, *, visible: bool) -> CostPoolView:
    lines = row.lines if visible else []
    return CostPoolView(
        id=row.id,
        sku_id=row.sku_id,
        quantity=row.quantity,
        currency=str(row.currency).strip() if visible else None,
        purchase_unit=shown_money(row.purchase_unit, visible=visible),
        first_mile_unit=shown_money(row.first_mile_unit, visible=visible),
        duty_unit=shown_money(row.duty_unit, visible=visible),
        import_tax_unit=shown_money(row.import_tax_unit, visible=visible),
        brokerage_unit=shown_money(row.brokerage_unit, visible=visible),
        storage_unit=shown_money(row.storage_unit, visible=visible),
        fx_reserve_unit=shown_money(row.fx_reserve_unit, visible=visible),
        landed_unit=shown_money(row.landed_unit, visible=visible),
        lines=lines,
        source=row.source if visible else "",
    )
