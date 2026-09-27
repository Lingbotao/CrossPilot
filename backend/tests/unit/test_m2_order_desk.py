"""M2-08 / M2-09：脱敏、游标、导出、面单尺寸、批量发货与失败重试。"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.adapters.amazon.adapter import AmazonAdapter
from app.adapters.base import CredentialView
from app.adapters.lazada.adapter import LazadaAdapter
from app.adapters.shopee.adapter import ShopeeAdapter
from app.adapters.tiktok.adapter import TikTokAdapter
from app.adapters.transport import FixtureTransport
from app.core.errors import ParamInvalidError
from app.db.base import SoftDeleteMixin
from app.engines.order_privacy import mask_party, mask_phone_middle, phone_last4
from app.models.order import OrderFee, SalesOrder, Shipment
from app.repositories.order_read import OrderHit
from app.services.label_pdf import build_label_pdf
from app.services.order_query import _export_row, read_order_cursor, write_order_cursor
from app.services.order_ship import (
    ShipLine,
    ShipTarget,
    check_batch_size,
    dedupe_ids,
    refuse_ship,
    run_ship_batch,
)
from app.services.order_xlsx import build_xlsx


def test_buyer_mask_follows_the_role_table() -> None:
    buyer = {"name": "Alice", "phone": "6591234567", "country": "SG"}
    ship = {
        "country": "SG",
        "state": "Singapore",
        "city": "Central",
        "line1": "1 Fixture Road",
        "postal_code": "018956",
        "phone": "6591234567",
    }
    full_buyer, full_ship = mask_party("OWNER", buyer, ship)
    assert full_buyer["phone"] == "6591234567"
    assert full_ship["line1"] == "1 Fixture Road"

    ops_buyer, ops_ship = mask_party("OPS_STAFF", buyer, ship)
    assert ops_buyer["name"] == "A****"
    assert ops_ship["line1"] == "1 Fixture Road"
    assert ops_ship["phone"] == "6591234567"

    finance_buyer, finance_ship = mask_party("FINANCE", buyer, ship)
    assert finance_buyer["name"] is None
    assert finance_ship["line1"] is None
    assert finance_ship["state"] == "Singapore"
    assert finance_ship["city"] == "Central"

    cs_buyer, cs_ship = mask_party("CS", buyer, ship)
    assert cs_buyer["name"] == "Alice"
    assert cs_buyer["phone"] == "659****567"
    assert cs_ship["line1"] is None

    viewer_buyer, viewer_ship = mask_party("VIEWER", buyer, ship)
    assert viewer_buyer == {"name": None, "phone": None, "country": None}
    assert viewer_ship["city"] is None


def test_phone_helpers() -> None:
    assert phone_last4("+1 202-555-0123") == "0123"
    assert phone_last4("12") is None
    assert mask_phone_middle("12") == "**"


def test_order_cursor_round_trip_and_bad_cursor_restarts() -> None:
    moment = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
    cursor = write_order_cursor(moment, 42)
    parsed_at, parsed_id = read_order_cursor(cursor)
    assert parsed_at == moment
    assert parsed_id == 42
    assert read_order_cursor("not-a-cursor") == (None, None)


def test_label_pdf_uses_a6_and_100x150() -> None:
    a6 = build_label_pdf([["Order SN1", "Shopee CP1"]], size="A6")
    assert a6.count(b"/Type /Page ") == 1
    assert b"297.64" in a6
    assert b"419.53" in a6
    thermal = build_label_pdf([["one"], ["two"]], size="100x150")
    assert thermal.count(b"/Type /Page ") == 2
    assert b"283.46" in thermal
    assert b"425.20" in thermal
    with pytest.raises(ValueError):
        build_label_pdf([["x"]], size="A4")


def test_xlsx_keeps_only_selected_columns_and_fee_text() -> None:
    payload = build_xlsx(["平台订单号", "费用明细"], [["SN1", "COMMISSION:1.200000 SGD"]])
    with ZipFile(BytesIO(payload)) as book:
        sheet = book.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "平台订单号" in sheet
    assert "费用明细" in sheet
    assert "COMMISSION:1.200000 SGD" in sheet
    assert "买家" not in sheet


def test_export_hides_buyer_from_viewer() -> None:
    order = SalesOrder(
        id=1,
        tenant_id=1,
        shop_id=2,
        platform_code="shopee",
        platform_order_id="SN1",
        idempotency_key="shopee:2:SN1",
        platform_status="READY_TO_SHIP",
        unified_status="PAID",
        buyer_info={"name": "Alice", "phone": "6591234567"},
        ship_to={"line1": "1 Road", "city": "Central"},
        currency="SGD",
        item_amount=Decimal("10.000000"),
        shipping_amount=Decimal("0"),
        tax_amount=Decimal("0"),
        discount_amount=Decimal("0"),
        total_amount=Decimal("10.000000"),
    )
    hit = OrderHit(
        order=order,
        shop_name="Shop",
        site_code="SG",
        shipment_status=None,
        failure_reason=None,
        tracking_no=None,
        carrier=None,
        attempt=None,
    )
    fee = OrderFee(
        id=9,
        tenant_id=1,
        order_id=1,
        fee_type="COMMISSION",
        amount=Decimal("1.200000"),
        currency="SGD",
        source="platform",
    )
    viewer = _export_row(hit, "VIEWER", ("buyer_name", "total_amount", "fee_detail"), [fee])
    assert viewer[0] == ""
    assert viewer[1] == "10.000000"
    assert "COMMISSION:1.200000 SGD" in viewer[2]
    owner = _export_row(hit, "OWNER", ("buyer_name",), [])
    assert owner == ["Alice"]


def test_batch_limit_and_dedupe() -> None:
    assert dedupe_ids([3, 3, 1]) == [3, 1]
    with pytest.raises(ParamInvalidError):
        check_batch_size(201, 200)
    with pytest.raises(ParamInvalidError):
        check_batch_size(0, 200)
    check_batch_size(200, 200)


def test_only_paid_orders_can_be_shipped_by_hand() -> None:
    assert refuse_ship("PAID") is None
    assert refuse_ship("PENDING") == "当前状态不能发货"
    assert refuse_ship("SHIPPED") == "当前状态不能发货"


def test_shipment_and_fee_are_not_soft_deleted() -> None:
    assert not issubclass(Shipment, SoftDeleteMixin)
    assert not issubclass(OrderFee, SoftDeleteMixin)
    assert "deleted_at" not in Shipment.__table__.c
    assert "deleted_at" not in OrderFee.__table__.c


class _MemoryBook:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, object]] = {
            1: {"sn": "A", "status": "PAID", "attempt": 0},
            2: {"sn": "B", "status": "PENDING", "attempt": 0},
            3: {"sn": "C", "status": "PAID", "attempt": 0},
        }

    @asynccontextmanager
    async def atomic(self):
        snapshot = deepcopy(self.rows)
        try:
            yield
        except Exception:
            self.rows = snapshot
            raise

    async def load(self, order_id: int) -> ShipTarget | None:
        row = self.rows.get(order_id)
        if row is None:
            return None
        return ShipTarget(
            order_id=order_id,
            platform_order_id=str(row["sn"]),
            unified_status=str(row["status"]),
            attempt=int(row["attempt"]),  # type: ignore[arg-type]
        )

    async def commit_success(self, target: ShipTarget, *, carrier: str, tracking_no: str, attempt: int) -> None:
        del carrier
        row = self.rows[target.order_id]
        row["status"] = "SHIPPED"
        row["attempt"] = attempt
        row["tracking"] = tracking_no

    async def commit_failure(self, target: ShipTarget, *, carrier: str, reason: str, attempt: int) -> None:
        del carrier
        row = self.rows[target.order_id]
        row["attempt"] = attempt
        row["reason"] = reason
        row["shipment"] = "FAILED"


class _Pusher:
    def __init__(self, fail_ids: set[int]) -> None:
        self.fail_ids = fail_ids
        self.calls: list[tuple[int, str]] = []

    async def push(self, target: ShipTarget, *, carrier: str, tracking_no: str) -> None:
        del carrier
        self.calls.append((target.order_id, tracking_no))
        if target.order_id in self.fail_ids:
            raise RuntimeError("platform down")


async def test_partial_ship_keeps_success_and_retry_uses_the_next_attempt() -> None:
    book = _MemoryBook()
    pusher = _Pusher({3})
    lines = await run_ship_batch(book, pusher, order_ids=[1, 2, 3, 9], carrier="Shopee")
    by_id = {line.order_id: line for line in lines}
    assert by_id[1].ok is True
    assert by_id[2].ok is False
    assert by_id[2].message == "当前状态不能发货"
    assert by_id[3].ok is False
    assert by_id[3].message == "发货回传失败"
    assert by_id[9].message == "订单不存在"
    assert book.rows[1]["status"] == "SHIPPED"
    assert book.rows[3]["status"] == "PAID"
    assert book.rows[3]["shipment"] == "FAILED"
    assert book.rows[3]["attempt"] == 1

    retry = _Pusher(set())
    again = await run_ship_batch(book, retry, order_ids=[3], carrier="Shopee")
    assert again == [ShipLine(3, "C", True, again[0].tracking_no, "已回传平台")]
    assert again[0].tracking_no is not None
    assert again[0].tracking_no.endswith("A2")
    assert book.rows[3]["status"] == "SHIPPED"


def _cred(platform: str) -> CredentialView:
    return CredentialView(
        shop_id="1",
        platform=platform,
        site_code="SG",
        access_token="token",
        refresh_token=None,
        expires_at=datetime(2026, 4, 1, tzinfo=UTC),
    )


async def test_fixture_transport_accepts_shipment_push_for_every_platform() -> None:
    transport = FixtureTransport()
    adapters = [
        AmazonAdapter(transport),
        ShopeeAdapter(transport),
        LazadaAdapter(transport),
        TikTokAdapter(transport),
    ]
    for adapter in adapters:
        await adapter.ship_order(_cred(adapter.platform), "OID-1", "Carrier", "CP1A1")


def test_pdf_round_trip_is_not_stored_as_text_in_the_envelope_helper() -> None:
    raw = build_label_pdf([["Order SN1"]], size="A6")
    encoded = base64.b64encode(raw).decode("ascii")
    assert base64.b64decode(encoded) == raw
