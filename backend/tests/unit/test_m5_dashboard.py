"""看板汇总口径：环比、履约、亏损榜、库存健康。不访问数据库。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.engines.dashboard import (
    AdFact,
    OrderFact,
    ProfitRow,
    SkuRank,
    SkuStock,
    assemble_shop_days,
    assess_inventory,
    black_friday,
    change_ratio,
    fold_lines,
    fulfillment_rate,
    previous_window,
    profit_roi,
    promo_marks,
    rank_skus,
    return_rate,
    shift_year,
    shipment_timing,
    summarize,
    turnover_days,
)


def test_change_ratio_is_missing_when_the_previous_base_is_zero() -> None:
    assert change_ratio(Decimal("10"), Decimal("0")) is None
    assert change_ratio(None, Decimal("5")) is None
    assert change_ratio(Decimal("15"), Decimal("10")) == Decimal("0.500000")


def test_windows_shift_without_dropping_a_leap_day() -> None:
    assert previous_window(date(2026, 10, 4), date(2026, 10, 10)) == (date(2026, 9, 27), date(2026, 10, 3))
    assert shift_year(date(2024, 2, 29)) == date(2023, 2, 28)


def test_black_friday_is_the_friday_after_thanksgiving() -> None:
    day = black_friday(2026)
    assert day.weekday() == 4
    assert day.month == 11
    assert 22 <= (day - timedelta(days=1)).day <= 28


def test_promo_marks_include_double_eleven() -> None:
    marks = promo_marks(date(2026, 11, 1), date(2026, 11, 30))
    assert "PROMO_1111" in {item.code for item in marks}
    assert "BLACK_FRIDAY" in {item.code for item in marks}


def test_shipment_timing_keeps_open_orders_out_of_the_rate() -> None:
    paid = datetime(2026, 10, 9, 1, tzinfo=UTC)
    deadline = paid + timedelta(hours=48)
    assert shipment_timing(status="CANCELLED", shipped_at=None, deadline=deadline, now=deadline) == "ignore"
    assert shipment_timing(status="PAID", shipped_at=None, deadline=deadline, now=paid) == "open"
    assert (
        shipment_timing(status="PAID", shipped_at=None, deadline=deadline, now=deadline + timedelta(minutes=1))
        == "late"
    )
    assert (
        shipment_timing(status="SHIPPED", shipped_at=paid + timedelta(hours=1), deadline=deadline, now=deadline)
        == "on_time"
    )
    assert fulfillment_rate(1, 0) == Decimal("1.000000")
    assert fulfillment_rate(0, 0) is None
    assert return_rate(1, 0) is None


def test_incomplete_cost_line_does_not_invent_a_share() -> None:
    lines = fold_lines([[{"code": "PURCHASE", "amount": "10.000000", "complete": True}]])
    purchase = next(item for item in lines if item.code == "PURCHASE")
    duty = next(item for item in lines if item.code == "DUTY")
    assert purchase.complete is True
    assert purchase.amount == Decimal("10.000000")
    assert duty.complete is False
    assert duty.amount is None


def test_profit_roi_uses_purchase_and_first_mile() -> None:
    assert profit_roi(Decimal("20"), Decimal("50"), Decimal("30")) == Decimal("0.250000")
    assert profit_roi(Decimal("20"), Decimal("0"), Decimal("0")) is None
    assert turnover_days(10, 0, 7) is None
    assert turnover_days(14, 7, 7) == Decimal("14.000000")


def test_assemble_keeps_gmv_when_profit_is_incomplete() -> None:
    day = date(2026, 10, 9)
    rows = assemble_shop_days(
        orders=[
            OrderFact(1, "amazon", "US", day, "USD", 2, Decimal("40"), 1, 1),
        ],
        profits=[
            ProfitRow(
                shop_id=1,
                stat_date=day,
                currency="USD",
                book_currency="CNY",
                revenue=Decimal("40"),
                book_revenue=None,
                net_profit=None,
                book_net_profit=None,
                complete=False,
                quantity=2,
                lines=[{"code": "PURCHASE", "amount": "10.000000", "complete": True}],
            )
        ],
        ads=[AdFact(1, day, "USD", Decimal("8"), Decimal("40"), 1)],
        returns=[],
        shops={1: ("amazon", "US")},
        book_currency="CNY",
    )
    assert len(rows) == 1
    assert rows[0].gmv == Decimal("40.000000")
    assert rows[0].profit_complete is False
    assert rows[0].net_profit is None
    assert rows[0].ad_spend == Decimal("8.000000")
    summary = summarize(rows)
    assert summary.currencies[0].acos == Decimal("0.200000")
    assert summary.currencies[0].net_margin is None


def test_inventory_marks_stockout_and_hides_partial_stale_amount() -> None:
    health = assess_inventory(
        [
            SkuStock(1, 0, 5, 0, Decimal("3"), "CNY"),
            SkuStock(2, 4, 10, 0, None, "CNY"),
            SkuStock(3, 8, 2, 3, Decimal("1"), "USD"),
        ]
    )
    assert health.stockout_sku_count == 1
    assert health.below_safe_sku_count == 1
    assert health.stale_sku_count == 1
    assert health.on_hand_qty == 12
    cny = next(item for item in health.stale_amounts if item.currency == "CNY")
    assert cny.complete is False
    assert cny.amount is None


def test_ranking_skips_incomplete_profit_and_flags_losses() -> None:
    ready = SkuRank(
        1, 10, "A", "USD", "CNY", 1, Decimal("20"), Decimal("5"), Decimal("30"), Decimal("0.25"), False, True
    )
    loss = SkuRank(
        2, 11, "B", "USD", "CNY", 1, Decimal("10"), Decimal("-2"), Decimal("-12"), Decimal("-0.2"), True, True
    )
    missing = SkuRank(3, 12, "C", "USD", "CNY", 1, Decimal("8"), None, None, None, False, False)
    top, bottom, losses = rank_skus([ready, loss, missing], limit=10)
    assert [item.sku_code for item in top] == ["A", "B"]
    assert [item.sku_code for item in bottom] == ["B", "A"]
    assert [item.sku_code for item in losses] == ["B"]
    assert missing not in top


def test_window_rejects_an_inverted_range_at_the_service_boundary() -> None:
    from app.core.errors import ParamInvalidError
    from app.services.dashboard import _window

    with pytest.raises(ParamInvalidError):
        _window(date(2026, 10, 10), date(2026, 10, 1))
