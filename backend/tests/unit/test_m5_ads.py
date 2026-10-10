"""M5-03 广告比率、亏损标记、只读拉取和成本隐藏。"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.amazon.adapter import AmazonAdapter, _amazon_ads
from app.adapters.base import (
    CredentialView,
    PageResult,
    PlatformAdapter,
    UnifiedAdCampaign,
    UnifiedAdDay,
    UnifiedAdKeyword,
)
from app.adapters.errors import AdapterError
from app.adapters.lazada.adapter import LazadaAdapter
from app.adapters.shopee.adapter import ShopeeAdapter
from app.adapters.tiktok.adapter import TikTokAdapter
from app.core.errors import ParamInvalidError
from app.engines.ads import (
    SUGGESTION_ACOS_ABOVE_MARGIN,
    SUGGESTION_MARGIN_MISSING,
    SUGGESTION_NO_SALES,
    as_percent,
    combine_sku_ads,
    gross_margin_ratio,
    plan_day,
    ratio,
    suggest_negative,
)
from app.engines.landed_cost import LANDED_CODES
from app.models.ads import AdCampaign
from app.schemas.ads import AdsSyncRequest
from app.services.ads import AdsService, _read_window

_DAY = date(2026, 10, 9)


def _lines(amount: str = "10.000000") -> list[dict[str, object]]:
    return [{"code": code, "amount": amount, "complete": True} for code in LANDED_CODES]


def test_ratios_stay_blank_when_the_denominator_is_zero() -> None:
    assert ratio(Decimal("1"), 0) is None
    assert ratio(3, 4) == Decimal("0.750000")
    assert as_percent(Decimal("0.750000")) == Decimal("75.000000")
    assert as_percent(None) is None


def test_acos_above_gross_margin_is_a_loss_and_zero_sales_is_a_loss() -> None:
    margin = gross_margin_ratio(Decimal("100"), _lines("10.000000"))
    assert margin == Decimal("0.300000")
    burning = plan_day(
        impressions=1000,
        clicks=50,
        orders=0,
        spend=Decimal("30"),
        sales=Decimal("0"),
        gross_margin=margin,
    )
    assert burning.loss_flag is True
    assert burning.suggestion_code == SUGGESTION_NO_SALES
    assert burning.acos is None
    flagged = plan_day(
        impressions=1000,
        clicks=50,
        orders=2,
        spend=Decimal("30"),
        sales=Decimal("40"),
        gross_margin=margin,
    )
    assert flagged.acos == Decimal("0.750000")
    assert flagged.loss_flag is True
    assert flagged.suggestion_code == SUGGESTION_ACOS_ABOVE_MARGIN
    healthy = plan_day(
        impressions=800,
        clicks=40,
        orders=6,
        spend=Decimal("4"),
        sales=Decimal("80"),
        gross_margin=margin,
    )
    assert healthy.loss_flag is False
    assert healthy.suggestion_code == ""
    missing = plan_day(
        impressions=10,
        clicks=1,
        orders=1,
        spend=Decimal("1"),
        sales=Decimal("4"),
        gross_margin=None,
    )
    assert missing.loss_flag is False
    assert missing.suggestion_code == SUGGESTION_MARGIN_MISSING
    idle = plan_day(
        impressions=10,
        clicks=0,
        orders=0,
        spend=Decimal("0"),
        sales=Decimal("0"),
        gross_margin=Decimal("-0.200000"),
    )
    assert idle.loss_flag is False


def test_incomplete_landed_cost_does_not_invent_a_margin() -> None:
    lines = _lines()
    lines[0] = {"code": "PURCHASE", "amount": None, "complete": False}
    assert gross_margin_ratio(Decimal("100"), lines) is None
    assert gross_margin_ratio(Decimal("0"), _lines()) is None


def test_zero_order_spend_is_a_suggested_negative_keyword() -> None:
    assert suggest_negative(spend=Decimal("0.000001"), orders=0) is True
    assert suggest_negative(spend=Decimal("0"), orders=0) is False
    assert suggest_negative(spend=Decimal("9"), orders=1) is False


def test_actual_ad_spend_replaces_the_estimated_line() -> None:
    value, formula, complete = combine_sku_ads(
        net_profits=[Decimal("20")],
        estimated_ads=[Decimal("6")],
        actual_spend=Decimal("10"),
    )
    assert complete is True
    assert value == Decimal("16.000000")
    assert formula == "20.000000 + 6.000000 - 10.000000"
    blank, reason, done = combine_sku_ads(net_profits=[], estimated_ads=[], actual_spend=Decimal("1"))
    assert blank is None and done is False
    assert "不完整" in reason
    missing, _, incomplete = combine_sku_ads(
        net_profits=[Decimal("1")],
        estimated_ads=[None],
        actual_spend=Decimal("1"),
    )
    assert missing is None and incomplete is False


def test_read_window_rejects_an_inverted_or_oversized_range() -> None:
    assert _read_window(_DAY, _DAY) == (_DAY, _DAY)
    with pytest.raises(ParamInvalidError):
        _read_window(date(2026, 10, 10), _DAY)
    with pytest.raises(ParamInvalidError):
        _read_window(date(2026, 1, 1), date(2026, 3, 1))


def test_amazon_report_without_campaigns_is_rejected() -> None:
    with pytest.raises(AdapterError):
        _amazon_ads({})


def test_four_platforms_map_their_own_ad_fields_and_have_no_write_api() -> None:
    forbidden = {"create_ad", "update_ad", "pause_ad", "update_bid", "reply_message"}
    assert forbidden.isdisjoint(dir(PlatformAdapter))
    now = datetime(2026, 10, 9, tzinfo=UTC)
    amazon = asyncio.run(_pull(AmazonAdapter(), "amazon", "US", now))
    assert [item.platform_campaign_id for item in amazon.items] == ["amz-loss", "amz-ok"]
    assert amazon.items[0].days[0].keywords[0].keyword == "cheap mug"
    assert amazon.items[0].days[0].spend == Decimal("30.000000")
    assert amazon.items[1].campaign_type == "SPONSORED_BRAND"
    shopee = asyncio.run(_pull(ShopeeAdapter(), "shopee", "SG", now))
    assert shopee.items[0].currency == "MYR"
    assert shopee.items[0].status == "ENABLED"
    lazada = asyncio.run(_pull(LazadaAdapter(), "lazada", "SG", now))
    assert lazada.items[0].platform_campaign_id == "lz-sp-1"
    tiktok = asyncio.run(_pull(TikTokAdapter(), "tiktok", "US", now))
    assert tiktok.items[0].status == "ENABLED"
    assert tiktok.items[0].days[0].orders == 4


def test_campaign_money_and_loss_flag_are_hidden_without_cost_visibility() -> None:
    service = AdsService(MagicMock())
    campaign = AdCampaign(
        tenant_id=1,
        shop_id=2,
        platform_code="amazon",
        platform_campaign_id="amz-loss",
        name="高花费",
        campaign_type="SPONSORED_PRODUCT",
        status="ENABLED",
        currency="USD",
        sku_id=3,
    )
    campaign.id = 8
    hidden = service._campaign_view(campaign, (1000, 50, 2, Decimal("30"), Decimal("40"), True), visible=False)
    assert hidden.spend is None
    assert hidden.acos is None
    assert hidden.loss_flag is False
    assert hidden.ctr == "5.000000"
    shown = service._campaign_view(campaign, (1000, 50, 2, Decimal("30"), Decimal("40"), True), visible=True)
    assert shown.spend == "30.000000"
    assert shown.acos == "75.000000"
    assert shown.loss_flag is True


def test_sync_request_accepts_a_string_shop_id() -> None:
    payload = AdsSyncRequest.model_validate({"shop_id": "15", "date_from": "2026-10-09", "date_to": "2026-10-09"})
    assert payload.shop_id == 15


def test_sync_marks_a_loss_and_a_negative_keyword() -> None:
    service = AdsService(MagicMock())
    shop = SimpleNamespace(id=9, tenant_id=3, status=1, platform_code="amazon")
    service.shops.get_or_404 = AsyncMock(return_value=shop)
    service.tasks.find_by_idempotency = AsyncMock(return_value=None)
    service.credentials.get_by_shop_id = AsyncMock(return_value=SimpleNamespace())
    service.listings.linked_sku_id = AsyncMock(return_value=4)
    service.profits.get_key = AsyncMock(return_value=SimpleNamespace(revenue=Decimal("100"), lines=_lines()))
    campaign = SimpleNamespace(id=11, tenant_id=3)
    service.campaigns.get_platform = AsyncMock(return_value=None)
    service.campaigns.add = AsyncMock(return_value=campaign)
    service.metrics.get_day = AsyncMock(return_value=None)
    stored_days: list[object] = []
    stored_keywords: list[object] = []

    async def add_day(row: object) -> object:
        stored_days.append(row)
        return row

    async def add_keyword(row: object) -> object:
        stored_keywords.append(row)
        return row

    service.metrics.add = AsyncMock(side_effect=add_day)
    service.keywords.get_row = AsyncMock(return_value=None)
    service.keywords.add = AsyncMock(side_effect=add_keyword)
    task = SimpleNamespace(id=99, stats={})
    service.tasks.add = AsyncMock(return_value=task)
    service.session.flush = AsyncMock()
    adapter = MagicMock()
    adapter.fetch_ads = AsyncMock(
        return_value=PageResult(
            items=[
                UnifiedAdCampaign(
                    platform_campaign_id="amz-loss",
                    name="高花费",
                    campaign_type="SPONSORED_PRODUCT",
                    status="ENABLED",
                    currency="USD",
                    platform_sku_id="MUG-1",
                    days=(
                        UnifiedAdDay(
                            stat_date=_DAY,
                            impressions=1000,
                            clicks=50,
                            spend=Decimal("30"),
                            sales=Decimal("40"),
                            orders=2,
                            currency="USD",
                            keywords=(
                                UnifiedAdKeyword(
                                    keyword="cheap mug",
                                    impressions=400,
                                    clicks=20,
                                    spend=Decimal("18"),
                                    sales=Decimal("0"),
                                    orders=0,
                                ),
                            ),
                        ),
                    ),
                )
            ]
        )
    )
    cred = CredentialView(
        shop_id="9",
        platform="amazon",
        site_code="US",
        access_token="token",
        refresh_token=None,
        expires_at=datetime(2026, 10, 10, tzinfo=UTC),
    )
    with (
        patch("app.services.ads.adapter_registry.get", return_value=adapter),
        patch("app.services.ads.view_from_row", return_value=cred),
    ):
        view = asyncio.run(
            service.sync(
                AdsSyncRequest(shop_id=9, date_from=_DAY, date_to=_DAY),
                tenant_id=3,
                actor_id=1,
                idempotency_key=" ads-1 ",
            )
        )
    assert view.loss_count == 1
    assert view.keywords == 1
    assert stored_days[0].loss_flag is True
    assert stored_days[0].suggestion_code == SUGGESTION_ACOS_ABOVE_MARGIN
    assert stored_keywords[0].suggest_negative is True
    assert task.stats["idempotency_key"] == "ads-1"


async def _pull(adapter: PlatformAdapter, platform: str, site: str, now: datetime) -> PageResult[UnifiedAdCampaign]:
    cred = CredentialView(
        shop_id="1",
        platform=platform,
        site_code=site,
        access_token="token",
        refresh_token=None,
        expires_at=now,
    )
    return await adapter.fetch_ads(cred, since=now, until=now)
