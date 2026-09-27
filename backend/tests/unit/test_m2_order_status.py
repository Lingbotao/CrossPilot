"""M2-06 九态状态机与平台默认映射。"""

from __future__ import annotations

from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.registry import SUPPORTED_PLATFORMS, adapter_registry
from app.db.base import SoftDeleteMixin, TenantMixin
from app.engines.order_status import (
    UNIFIED_STATUS_CODES,
    DecisionKind,
    StatusChangeSource,
    UnifiedStatus,
    decide_status,
    merge_mapping,
    resolve_target,
)
from app.models.config import PlatformStatusMapping
from app.models.order import OrderStatusLog, SalesOrder


def test_nine_statuses_match_prd() -> None:
    assert [status.value for status in UnifiedStatus] == [
        "PENDING",
        "PAID",
        "SHIPPED",
        "DELIVERED",
        "COMPLETED",
        "CANCELLED",
        "REFUNDING",
        "REFUNDED",
        "RETURNED",
    ]


def test_new_order_takes_the_mapped_status() -> None:
    decision = decide_status(None, UnifiedStatus.PAID, source=StatusChangeSource.SYSTEM)
    assert decision.kind is DecisionKind.APPLY
    assert decision.from_status is None
    assert decision.to_status is UnifiedStatus.PAID
    assert decision.unmapped is False


def test_unknown_platform_status_does_not_become_paid() -> None:
    assert resolve_target("BRAND_NEW", {"READY_TO_SHIP": "PAID"}) is None
    created = decide_status(None, None, source=StatusChangeSource.SYSTEM)
    assert created.to_status is UnifiedStatus.PENDING
    assert created.unmapped is True
    kept = decide_status(UnifiedStatus.SHIPPED, None, source=StatusChangeSource.WEBHOOK)
    assert kept.kind is DecisionKind.UNCHANGED
    assert kept.to_status is UnifiedStatus.SHIPPED
    assert kept.unmapped is True


def test_system_and_webhook_can_skip_forward_on_the_main_chain() -> None:
    for source in (StatusChangeSource.SYSTEM, StatusChangeSource.WEBHOOK):
        decision = decide_status(UnifiedStatus.PENDING, UnifiedStatus.SHIPPED, source=source)
        assert decision.kind is DecisionKind.APPLY
        assert decision.to_status is UnifiedStatus.SHIPPED


def test_manual_cannot_skip_forward() -> None:
    decision = decide_status(UnifiedStatus.PENDING, UnifiedStatus.SHIPPED, source=StatusChangeSource.MANUAL)
    assert decision.kind is DecisionKind.REJECT
    assert decision.to_status is UnifiedStatus.PENDING


def test_backward_move_is_rejected() -> None:
    decision = decide_status(UnifiedStatus.SHIPPED, UnifiedStatus.PAID, source=StatusChangeSource.SYSTEM)
    assert decision.kind is DecisionKind.REJECT
    assert decision.to_status is UnifiedStatus.SHIPPED


def test_same_status_is_unchanged() -> None:
    decision = decide_status(UnifiedStatus.PAID, UnifiedStatus.PAID, source=StatusChangeSource.SYSTEM)
    assert decision.kind is DecisionKind.UNCHANGED
    assert decision.unmapped is False


def test_cancelled_is_terminal() -> None:
    decision = decide_status(UnifiedStatus.CANCELLED, UnifiedStatus.PAID, source=StatusChangeSource.WEBHOOK)
    assert decision.kind is DecisionKind.REJECT


def test_after_sales_edges() -> None:
    refunded = decide_status(UnifiedStatus.DELIVERED, UnifiedStatus.REFUNDED, source=StatusChangeSource.SYSTEM)
    assert refunded.kind is DecisionKind.APPLY
    returned = decide_status(UnifiedStatus.REFUNDED, UnifiedStatus.RETURNED, source=StatusChangeSource.SYSTEM)
    assert returned.kind is DecisionKind.APPLY
    back = decide_status(UnifiedStatus.RETURNED, UnifiedStatus.REFUNDED, source=StatusChangeSource.SYSTEM)
    assert back.kind is DecisionKind.APPLY
    manual_back = decide_status(UnifiedStatus.RETURNED, UnifiedStatus.REFUNDED, source=StatusChangeSource.MANUAL)
    assert manual_back.kind is DecisionKind.APPLY
    unpaid_refund = decide_status(UnifiedStatus.PENDING, UnifiedStatus.REFUNDING, source=StatusChangeSource.SYSTEM)
    assert unpaid_refund.kind is DecisionKind.REJECT
    cancel_paid = decide_status(UnifiedStatus.PAID, UnifiedStatus.CANCELLED, source=StatusChangeSource.SYSTEM)
    assert cancel_paid.kind is DecisionKind.APPLY
    cancel_done = decide_status(UnifiedStatus.COMPLETED, UnifiedStatus.CANCELLED, source=StatusChangeSource.WEBHOOK)
    assert cancel_done.kind is DecisionKind.REJECT
    paid_return = decide_status(UnifiedStatus.PAID, UnifiedStatus.RETURNED, source=StatusChangeSource.SYSTEM)
    assert paid_return.kind is DecisionKind.REJECT
    refund_back_to_ship = decide_status(
        UnifiedStatus.REFUNDING, UnifiedStatus.SHIPPED, source=StatusChangeSource.SYSTEM
    )
    assert refund_back_to_ship.kind is DecisionKind.REJECT


def test_manual_adjacent_ship() -> None:
    decision = decide_status(UnifiedStatus.PAID, UnifiedStatus.SHIPPED, source=StatusChangeSource.MANUAL)
    assert decision.kind is DecisionKind.APPLY


def test_stored_mapping_overrides_default_and_drops_invalid() -> None:
    merged = merge_mapping(
        {"READY_TO_SHIP": "PAID", "UNPAID": "PENDING"},
        {"READY_TO_SHIP": "CANCELLED", "CUSTOM": "NOT_A_STATUS", "UNPAID": "PENDING"},
    )
    assert merged["READY_TO_SHIP"] == "CANCELLED"
    assert merged["UNPAID"] == "PENDING"
    assert "CUSTOM" not in merged
    assert resolve_target("READY_TO_SHIP", merged) is UnifiedStatus.CANCELLED


def test_prd_examples_map_to_paid() -> None:
    register_builtin_adapters()
    assert adapter_registry.get("shopee").unified_status("READY_TO_SHIP") == "PAID"
    assert adapter_registry.get("lazada").unified_status("pending") == "PAID"
    assert adapter_registry.get("amazon").unified_status("Unshipped") == "PAID"
    assert adapter_registry.get("tiktok").unified_status("AWAITING_SHIPMENT") == "PAID"


def test_adapter_maps_stay_inside_nine_states() -> None:
    register_builtin_adapters()
    for platform in SUPPORTED_PLATFORMS:
        adapter = adapter_registry.get(platform)
        for unified in adapter.status_mapping().values():
            assert unified in UNIFIED_STATUS_CODES
        assert adapter.unified_status("not-a-real-status") == ""


def test_status_log_is_tenant_ledger_and_mapping_is_global() -> None:
    assert issubclass(OrderStatusLog, TenantMixin)
    assert not issubclass(OrderStatusLog, SoftDeleteMixin)
    assert not issubclass(PlatformStatusMapping, TenantMixin)
    names = {constraint.name for constraint in SalesOrder.__table__.constraints}
    assert "ck_sales_order_unified_status" in names
    log_names = {constraint.name for constraint in OrderStatusLog.__table__.constraints}
    assert "ck_order_status_log_to_status" in log_names
    assert "ck_order_status_log_source" in log_names
    mapping_names = {constraint.name for constraint in PlatformStatusMapping.__table__.constraints}
    assert "ck_platform_status_mapping_unified_status" in mapping_names
