"""采购状态机、收货和头程分摊。不访问数据库。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.errors import ErrorCode
from app.engines.landed_cost import LANDED_CODES, CostLine, LandedCostResult
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
    plan_receive,
    should_move_stock,
    transit_delta_for_qty_change,
    transit_region,
    weighted_unit,
)
from app.models.enums import AuditAction
from app.schemas.purchase import (
    CostPoolView,
    InTransitView,
    PurchaseOrderCreate,
    PurchaseOrderPatch,
    PurchaseOrderView,
    ReceiptCreate,
    ReplenishmentConvert,
    ShipmentCreate,
    ShipmentView,
    SkuSupplierReplace,
    SupplierPatch,
    SupplierView,
    SupplierWrite,
    shown_money,
)


def test_purchase_contract_codes() -> None:
    assert ErrorCode.PURCHASE_STATE_INVALID == 60008
    assert ErrorCode.RECEIPT_QUANTITY_INVALID == 60009
    assert ErrorCode.SUPPLIER_DEFAULT_CONFLICT == 60010
    assert ErrorCode.COST_POOL_INCOMPLETE == 70008
    assert AuditAction.PURCHASE_CHANGE in AuditAction.ALL


def test_submit_approve_and_cancel_before_receipt() -> None:
    assert assert_transition("DRAFT", "submit", received=0) == "PENDING"
    assert assert_transition("PENDING", "approve", received=0) == "APPROVED"
    assert assert_transition("APPROVED", "cancel", received=0) == "CANCELLED"
    with pytest.raises(PurchaseRuleError):
        assert_transition("APPROVED", "cancel", received=1)
    with pytest.raises(PurchaseRuleError):
        assert_transition("DRAFT", "approve", received=0)


def test_partial_over_and_short_receipt() -> None:
    partial = plan_receive(ordered=10, received=0, short=0, quantity=4, disposition="RECEIVE", allow_over=False)
    assert partial.receive_qty == 4
    assert partial.transit_release == 4
    assert partial.new_received == 4
    with pytest.raises(PurchaseRuleError, match="超收"):
        plan_receive(ordered=10, received=8, short=0, quantity=4, disposition="RECEIVE", allow_over=False)
    over = plan_receive(ordered=10, received=8, short=0, quantity=4, disposition="RECEIVE", allow_over=True)
    assert over.receive_qty == 4
    assert over.transit_release == 2
    assert over.new_received == 12
    short = plan_receive(ordered=10, received=4, short=0, quantity=0, disposition="SHORT", allow_over=False)
    assert short.receive_qty == 0
    assert short.transit_release == 6
    assert short.new_short == 6
    assert next_receipt_status([(10, 4, 0), (5, 5, 0)]) == "PARTIAL"
    assert next_receipt_status([(10, 4, 6)]) == "RECEIVED"


def test_quantity_change_moves_only_posted_transit() -> None:
    assert transit_delta_for_qty_change("DRAFT", 10, 0, 0, 12) == 0
    assert transit_delta_for_qty_change("APPROVED", 10, 4, 0, 12) == 2
    assert transit_delta_for_qty_change("PARTIAL", 10, 4, 1, 8) == -2
    with pytest.raises(PurchaseRuleError):
        transit_delta_for_qty_change("APPROVED", 10, 4, 1, 4)


def test_allocation_sums_to_total_and_keeps_remainder() -> None:
    lines = [
        AllocInput(1, 1, Decimal("1"), Decimal("1"), Decimal("1")),
        AllocInput(2, 1, Decimal("1"), Decimal("1"), Decimal("1")),
        AllocInput(3, 1, Decimal("1"), Decimal("1"), Decimal("1")),
    ]
    outputs = allocate("WEIGHT", Decimal("1"), "USD", lines)
    assert sum((row.allocated_cost for row in outputs), Decimal("0")) == Decimal("1.000000")
    assert outputs[0].allocated_cost == Decimal("0.333334")
    assert "尾差" in outputs[0].formula
    with pytest.raises(PurchaseRuleError):
        allocate("VOLUME", Decimal("1"), "USD", [AllocInput(1, 1, Decimal("1"), Decimal("0"), Decimal("1"))])


def test_weighted_average_and_stock_rules() -> None:
    assert weighted_unit(0, Decimal("1"), 4, Decimal("3")) == Decimal("3.000000")
    assert weighted_unit(1, Decimal("2"), 1, Decimal("4")) == Decimal("3.000000")
    assert transit_region("LOCAL") == "DOMESTIC"
    assert transit_region("FBA") == "OVERSEAS"
    with pytest.raises(PurchaseRuleError):
        transit_region("PLATFORM")
    assert should_move_stock(from_id=1, to_id=2, po_warehouse_type="LOCAL") is True
    assert should_move_stock(from_id=1, to_id=2, po_warehouse_type="OVERSEAS") is False
    assert should_move_stock(from_id=1, to_id=1, po_warehouse_type="LOCAL") is False


def test_first_mile_explanation_keeps_amount() -> None:
    lines = tuple(CostLine(code, code, Decimal("1.000000"), "USD", "raw", "config", True) for code in LANDED_CODES)
    result = LandedCostResult(lines, "USD", Decimal("7.000000"), None, None, None, True, False)
    bound = bind_first_mile(result, "真实分摊", "头程物流单")
    first = next(line for line in bound.lines if line.code == "FIRST_MILE")
    assert first.amount == Decimal("1.000000")
    assert first.formula == "真实分摊"
    assert first.source == "头程物流单"
    assert bound.landed_cost == Decimal("7.000000")
    assert incomplete_landed(bound) == []


def test_cost_amounts_are_hidden_without_visibility() -> None:
    assert shown_money(Decimal("1.250000"), visible=True) == "1.250000"
    assert shown_money(None, visible=True) is None
    assert shown_money(Decimal("1.250000"), visible=False) is None


def test_remaining_state_and_allocation_edges() -> None:
    assert assert_transition("PENDING", "reject", received=0) == "DRAFT"
    assert assert_transition("RECEIVED", "close", received=1) == "CLOSED"
    assert_can_edit("PENDING")
    assert_can_receive("PARTIAL")
    with pytest.raises(PurchaseRuleError):
        assert_can_edit("CLOSED")
    with pytest.raises(PurchaseRuleError):
        assert_can_receive("DRAFT")
    assert next_receipt_status([(10, 0, 0)]) == "APPROVED"
    assert "本次到仓" in average_formula("采购", 0, Decimal("0"), 2, Decimal("1"), Decimal("1"), "CNY")
    assert "×" in average_formula("采购", 1, Decimal("1"), 1, Decimal("3"), Decimal("2"), "CNY")
    with pytest.raises(PurchaseRuleError):
        weighted_unit(0, Decimal("1"), 0, Decimal("1"))
    with pytest.raises(PurchaseRuleError):
        plan_receive(ordered=1, received=1, short=0, quantity=0, disposition="SHORT", allow_over=False)
    with pytest.raises(PurchaseRuleError):
        allocate("CHARGEABLE", Decimal("1"), "USD", [AllocInput(1, 1, Decimal("1"), Decimal("1"), Decimal("1"))])
    value = allocate("VALUE", Decimal("2"), "USD", [AllocInput(1, 2, Decimal("1"), Decimal("1"), Decimal("1"))])
    assert value[0].allocated_cost == Decimal("2.000000")
    broken = LandedCostResult(
        (CostLine("FIRST_MILE", "头程", None, "USD", "raw", "config", False),),
        "USD",
        None,
        None,
        None,
        None,
        False,
        False,
    )
    rebound = bind_first_mile(broken, "真实分摊", "头程物流单")
    assert rebound.complete is False
    assert incomplete_landed(rebound) == ["FIRST_MILE"]


def test_purchase_payloads_normalize_money_and_ids() -> None:
    supplier = SupplierWrite(name=" 工厂 ", contact=" 张三 ", settlement_type="credit", credit_days=30, rating=4)
    assert supplier.name == "工厂"
    assert supplier.settlement_type == "CREDIT"
    patched = SupplierPatch(name=" 新名 ", settlement_type=None, contact=None)
    assert patched.name == "新名"
    assert patched.settlement_type is None
    links = SkuSupplierReplace(links=[{"sku_id": "12", "is_default": True}])  # type: ignore[list-item]
    assert links.links[0].sku_id == 12
    order = PurchaseOrderCreate(
        supplier_id="3",  # type: ignore[arg-type]
        warehouse_id="4",  # type: ignore[arg-type]
        currency=" cny ",
        note=" 备注 ",
        lines=[{"sku_id": "9", "quantity": 2, "unit_price": "1.5", "tax_included": True}],  # type: ignore[list-item]
    )
    assert order.currency == "CNY"
    assert order.lines[0].unit_price == Decimal("1.5")
    assert PurchaseOrderPatch(note=" 变更 ").note == "变更"
    receipt = ReceiptCreate(
        disposition=" receive ",
        note=" 收 ",
        lines=[{"item_id": "8", "quantity": 1}],  # type: ignore[list-item]
    )
    assert receipt.disposition == "RECEIVE"
    shipment = ShipmentCreate(
        forwarder=" 货代 ",
        channel=" air ",
        container_no=" ",
        destination_market=" us ",
        cost_total="10",
        currency="usd",
        alloc_method="weight",
        purchase_order_id="",
        lines=[{"sku_id": "9", "quantity": 1}],  # type: ignore[list-item]
    )
    assert shipment.channel == "AIR"
    assert shipment.destination_market == "US"
    assert shipment.alloc_method == "WEIGHT"
    assert shipment.purchase_order_id is None
    convert = ReplenishmentConvert(
        sku_ids=["1", "2"],  # type: ignore[list-item]
        warehouse_id="5",  # type: ignore[arg-type]
        supplier_id="",
        currency="cny",
        unit_price="",
    )
    assert convert.sku_ids == [1, 2]
    assert convert.supplier_id is None
    assert convert.unit_price is None
    with pytest.raises(ValidationError):
        SupplierWrite(name="工厂", settlement_type="CASH")
    with pytest.raises(ValidationError):
        PurchaseOrderCreate(
            supplier_id=1,
            warehouse_id=1,
            currency="CN",
            lines=[{"sku_id": 1, "quantity": 1, "unit_price": "-1"}],
        )
    with pytest.raises(ValidationError):
        ShipmentCreate(
            forwarder="货代",
            channel="AIR",
            destination_market="USA",
            cost_total="0",
            currency="USD",
            alloc_method="WEIGHT",
            lines=[{"sku_id": 1, "quantity": 1}],
        )
    with pytest.raises(ValidationError):
        ReplenishmentConvert(sku_ids="1", warehouse_id=1, currency="CNY")  # type: ignore[arg-type]


def test_purchase_views_serialize_ids_as_strings() -> None:
    line = {
        "id": 1,
        "sku_id": 2,
        "sku_code": "SKU",
        "quantity": 3,
        "received_qty": 0,
        "short_qty": 0,
        "open_qty": 3,
        "unit_price": "1.000000",
        "currency": "CNY",
        "tax_included": False,
        "expected_on": date(2026, 10, 10),
    }
    order = PurchaseOrderView(
        id=10,
        supplier_id=11,
        supplier_name="工厂",
        warehouse_id=12,
        warehouse_name="国内仓",
        warehouse_type="LOCAL",
        status="DRAFT",
        currency="CNY",
        total_amount="3.000000",
        expected_on=None,
        note="",
        lines=[line],
    )
    dumped = order.model_dump()
    assert dumped["id"] == "10"
    assert dumped["lines"][0]["sku_id"] == "2"
    supplier = SupplierView(
        id=1,
        name="工厂",
        contact="",
        settlement_type="MONTHLY",
        credit_days=0,
        rating=None,
        skus=[{"sku_id": 2, "sku_code": "SKU", "is_default": True}],
    )
    assert supplier.model_dump()["skus"][0]["sku_id"] == "2"
    transit = InTransitView(
        domestic_qty=1,
        overseas_qty=0,
        lines=[
            {
                "region": "DOMESTIC",
                "warehouse_id": 3,
                "warehouse_name": "国内仓",
                "warehouse_type": "LOCAL",
                "sku_id": 2,
                "sku_code": "SKU",
                "quantity": 1,
            }
        ],
    )
    assert transit.model_dump()["lines"][0]["warehouse_id"] == "3"
    pool = CostPoolView(
        id=4,
        sku_id=2,
        quantity=1,
        currency="CNY",
        purchase_unit="1.000000",
        first_mile_unit="0.000000",
        duty_unit=None,
        import_tax_unit=None,
        brokerage_unit=None,
        storage_unit=None,
        fx_reserve_unit=None,
        landed_unit="1.000000",
        lines=[],
        source="到仓",
    )
    shipment = ShipmentView(
        id=5,
        purchase_order_id=None,
        from_warehouse_id=6,
        to_warehouse_id=None,
        forwarder="货代",
        channel="AIR",
        container_no="",
        destination_market="US",
        cost_total="1.000000",
        currency="USD",
        alloc_method="WEIGHT",
        storage_days=0,
        status="POSTED",
        allocations=[
            {
                "id": 7,
                "sku_id": 2,
                "sku_code": "SKU",
                "quantity": 1,
                "allocated_cost": "1.000000",
                "currency": "USD",
                "method": "WEIGHT",
                "formula": "公式",
                "source": "头程",
            }
        ],
        pools=[pool],
    )
    body = shipment.model_dump()
    assert body["purchase_order_id"] is None
    assert body["from_warehouse_id"] == "6"
    assert body["pools"][0]["id"] == "4"
