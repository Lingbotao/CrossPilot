"""M2-10 / M2-11：审核、异常、退货状态、同步部分成功与陈旧提示。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.engines.order_desk import (
    AmountRule,
    address_invalid,
    amount_needs_review,
    classify_exceptions,
    decide_return,
    next_review_status,
    restock_after_refund,
    review_blocks_ship,
    ship_window,
)
from app.models.enums import SyncStatus
from app.services.shop_health import freshness_line, resolve_sync_status


def test_amount_rule_holds_only_the_same_currency() -> None:
    rules = [AmountRule(currency="USD", amount_gt=Decimal("500"))]
    assert amount_needs_review(Decimal("500.01"), "USD", rules)
    assert not amount_needs_review(Decimal("500"), "USD", rules)
    assert not amount_needs_review(Decimal("900"), "SGD", rules)


def test_manual_review_is_not_reopened_by_a_later_sync() -> None:
    rules = [AmountRule(currency="USD", amount_gt=Decimal("10"))]
    assert next_review_status("APPROVED", total=Decimal("99"), currency="USD", rules=rules) == "APPROVED"
    assert next_review_status("AUTO_PASSED", total=Decimal("99"), currency="USD", rules=rules) == "PENDING"
    assert next_review_status("PENDING", total=Decimal("1"), currency="USD", rules=rules) == "AUTO_PASSED"


def test_pending_review_blocks_shipping() -> None:
    assert review_blocks_ship("PENDING") == "订单待人工审核，不能发货"
    assert review_blocks_ship("REJECTED") == "订单审核未通过，不能发货"
    assert review_blocks_ship("AUTO_PASSED") is None
    assert review_blocks_ship("APPROVED") is None


def test_exceptions_cover_sku_address_and_the_ship_window() -> None:
    paid = datetime(2026, 1, 15, 12, tzinfo=UTC)
    overdue = classify_exceptions(
        unified_status="PAID",
        review_status="AUTO_PASSED",
        ship_to={"country": "SG"},
        sku_unmatched=True,
        paid_at=paid,
        now=paid + timedelta(hours=49),
        sla_hours=48,
        warn_hours=4,
    )
    assert overdue == ["SKU_UNMATCHED", "ADDRESS_INVALID", "SHIP_OVERDUE"]
    soon = ship_window(
        unified_status="PAID",
        paid_at=paid,
        now=paid + timedelta(hours=45),
        sla_hours=48,
        warn_hours=4,
    )
    assert soon == "SHIP_DUE_SOON"
    assert (
        ship_window(unified_status="SHIPPED", paid_at=paid, now=paid + timedelta(hours=80), sla_hours=48, warn_hours=4)
        is None
    )
    assert not address_invalid({"country": "SG", "city": "Central"})


def test_return_flow_and_deferred_restock() -> None:
    assert decide_return("REQUESTED", "approve") == "APPROVED"
    assert decide_return("APPROVED", "refund") == "REFUNDED"
    assert decide_return("REQUESTED", "refund") is None
    assert decide_return("REJECTED", "approve") is None
    assert restock_after_refund(restock_flag=True) == "DEFERRED"
    assert restock_after_refund(restock_flag=False) == "NONE"


def test_partial_sync_and_stale_copy() -> None:
    assert resolve_sync_status(failed=0, landed=3, errored=False) is SyncStatus.SUCCESS
    assert resolve_sync_status(failed=1, landed=2, errored=False) is SyncStatus.PARTIAL
    assert resolve_sync_status(failed=1, landed=0, errored=True) is SyncStatus.FAILED
    now = datetime(2026, 1, 15, 8, tzinfo=UTC)
    failed = freshness_line(
        last_sync_status=int(SyncStatus.FAILED),
        last_sync_at=now - timedelta(minutes=10),
        now=now,
        stale=timedelta(hours=1),
    )
    assert failed is not None
    assert "可能不是最新" in failed
    assert (
        freshness_line(
            last_sync_status=int(SyncStatus.SUCCESS),
            last_sync_at=now,
            now=now,
            stale=timedelta(hours=1),
        )
        is None
    )
