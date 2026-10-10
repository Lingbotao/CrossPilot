"""广告只读同步与分析（M5-03 / F8-01~05）。

手动补拉和定时任务走同一条入库路径。平台侧没有创建、改价或暂停接口。
花费、ACOS、ROAS、毛利率和扣广告后的净利对没有成本可见性的角色留空。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import CredentialView, UnifiedAdCampaign, UnifiedAdKeyword
from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError
from app.adapters.registry import adapter_registry
from app.core.config import settings
from app.core.errors import AdsReportInvalidError, AppError, ErrorCode, ParamInvalidError, PlatformUnsupportedError
from app.core.pagination import PageData, PageInfo, build_cursor_page, decode_cursor, encode_cursor
from app.engines.ads import (
    DayMetrics,
    as_percent,
    combine_sku_ads,
    gross_margin_ratio,
    line_amount,
    plan_day,
    ratio,
    suggest_negative,
)
from app.engines.landed_cost import quantize
from app.models.ads import AdCampaign, AdKeywordMetric, AdMetricDaily
from app.models.enums import ShopStatus, SyncStatus, SyncTrigger
from app.models.finance import SkuProfitDaily
from app.models.platform import Shop, SyncTask
from app.repositories.ads import AdCampaignRepository, AdKeywordRepository, AdMetricRepository
from app.repositories.finance import SkuProfitRepository
from app.repositories.listing import ListingRepository
from app.repositories.platform import ShopCredentialRepository, ShopRepository, SyncTaskRepository
from app.schemas.ads import (
    AdsCampaignDetail,
    AdsCampaignView,
    AdsDayView,
    AdsKeywordView,
    AdsLossView,
    AdsOverviewView,
    AdsSkuProfitView,
    AdsSyncRequest,
    AdsSyncView,
    AdsTotalView,
    shown_id,
    shown_money,
)
from app.schemas.common import money_to_str
from app.services.credential_service import view_from_row

_ZERO = Decimal("0")


class AdsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shops = ShopRepository(session)
        self.credentials = ShopCredentialRepository(session)
        self.tasks = SyncTaskRepository(session)
        self.campaigns = AdCampaignRepository(session)
        self.metrics = AdMetricRepository(session)
        self.keywords = AdKeywordRepository(session)
        self.listings = ListingRepository(session)
        self.profits = SkuProfitRepository(session)
        register_builtin_adapters()

    async def sync(
        self,
        payload: AdsSyncRequest,
        *,
        tenant_id: int,
        actor_id: int | None,
        idempotency_key: str | None,
        trigger: SyncTrigger = SyncTrigger.MANUAL,
    ) -> AdsSyncView:
        start, end = _sync_window(payload.date_from, payload.date_to)
        key = _clean_key(idempotency_key)
        if key:
            existing = await self.tasks.find_by_idempotency("ads", key)
            if existing is not None and int(existing.status) == int(SyncStatus.SUCCESS):
                summary = existing.stats.get("summary")
                if isinstance(summary, dict):
                    return AdsSyncView.model_validate(summary)
            if existing is not None:
                raise AppError("相同幂等键的广告同步不能重复执行", code=ErrorCode.IDEMPOTENCY_CONFLICT)
        shop = await self.shops.get_or_404(payload.shop_id)
        if int(shop.status) != int(ShopStatus.ACTIVE):
            raise AppError("店铺当前不能同步广告", code=ErrorCode.SHOP_GRANT_EXPIRED)
        credential = await self.credentials.get_by_shop_id(shop.id)
        if credential is None:
            raise AppError("店铺还没有授权，不能同步广告", code=ErrorCode.SHOP_GRANT_EXPIRED)
        pulled = await self._pull(shop, view_from_row(shop, credential), start, end)
        counts = await self._store(shop, pulled, start, end, actor_id)
        task = await self.tasks.add(
            SyncTask(
                tenant_id=tenant_id,
                shop_id=shop.id,
                module="ads",
                trigger_type=int(trigger),
                status=int(SyncStatus.SUCCESS),
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                since=datetime.combine(start, time.min, tzinfo=UTC),
                until=datetime.combine(end, time.max, tzinfo=UTC),
                stats={},
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        view = AdsSyncView(
            task_id=str(task.id),
            shop_id=str(shop.id),
            campaigns=counts[0],
            days=counts[1],
            keywords=counts[2],
            loss_count=counts[3],
        )
        stats: dict[str, object] = {"summary": view.model_dump()}
        if key:
            stats["idempotency_key"] = key
        task.stats = stats
        await self.session.flush()
        return view

    async def overview(
        self,
        *,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
        campaign_id: int | None,
        visible: bool,
    ) -> AdsOverviewView:
        start, end = _read_window(date_from, date_to)
        platform = self._platform(platform_code)
        impressions, clicks, orders = await self.metrics.activity(
            date_from=start,
            date_to=end,
            shop_id=shop_id,
            platform_code=platform,
            campaign_id=campaign_id,
        )
        totals: list[AdsTotalView] = []
        for currency, spend, sales in await self.metrics.money_by_currency(
            date_from=start,
            date_to=end,
            shop_id=shop_id,
            platform_code=platform,
            campaign_id=campaign_id,
        ):
            planned = plan_day(
                impressions=impressions,
                clicks=clicks,
                orders=orders,
                spend=spend,
                sales=sales,
                gross_margin=None,
            )
            totals.append(
                AdsTotalView(
                    currency=currency,
                    spend=shown_money(planned.spend, visible=visible),
                    sales=shown_money(planned.sales, visible=visible),
                    acos=shown_money(as_percent(planned.acos), visible=visible),
                    roas=shown_money(planned.roas, visible=visible),
                )
            )
        return AdsOverviewView(
            impressions=impressions,
            clicks=clicks,
            orders=orders,
            ctr=shown_money(as_percent(ratio(clicks, impressions)), visible=True),
            cvr=shown_money(as_percent(ratio(orders, clicks)), visible=True),
            totals=totals,
        )

    async def list_campaigns(
        self,
        *,
        limit: int,
        cursor: str | None,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
        campaign_id: int | None,
        visible: bool,
    ) -> PageData[AdsCampaignView]:
        start, end = _read_window(date_from, date_to)
        rows = await self.campaigns.list_cursor(
            limit=limit,
            before_id=_before_id(cursor),
            shop_id=shop_id,
            platform_code=self._platform(platform_code),
            campaign_id=campaign_id,
        )
        page = build_cursor_page(rows, limit)
        sums = await self.metrics.sums_for([row.id for row in page.items], start, end)
        items = [self._campaign_view(row, sums.get(row.id), visible=visible) for row in page.items]
        return PageData[AdsCampaignView](items=items, page_info=page.page_info)

    async def campaign_detail(
        self,
        campaign_id: int,
        *,
        date_from: date,
        date_to: date,
        visible: bool,
    ) -> AdsCampaignDetail:
        start, end = _read_window(date_from, date_to)
        campaign = await self.campaigns.get_or_404(campaign_id)
        sums = await self.metrics.sums_for([campaign.id], start, end)
        days = await self.metrics.list_days(campaign.id, start, end)
        return AdsCampaignDetail(
            campaign=self._campaign_view(campaign, sums.get(campaign.id), visible=visible),
            days=[_day_view(row, visible=visible) for row in days],
        )

    async def list_keywords(
        self,
        *,
        limit: int,
        cursor: str | None,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
        campaign_id: int | None,
        only_negative: bool,
        visible: bool,
    ) -> PageData[AdsKeywordView]:
        start, end = _read_window(date_from, date_to)
        before_spend, before_id = _spend_cursor(cursor)
        rows = await self.keywords.list_cursor(
            limit=limit,
            before_spend=before_spend,
            before_id=before_id,
            date_from=start,
            date_to=end,
            shop_id=shop_id,
            platform_code=self._platform(platform_code),
            campaign_id=campaign_id,
            only_negative=only_negative,
        )
        visible_rows, next_cursor, has_more = _spend_page(rows, limit)
        items = [_keyword_view(metric, campaign, visible=visible) for metric, campaign in visible_rows]
        return PageData[AdsKeywordView](items=items, page_info=PageInfo(cursor=next_cursor, has_more=has_more))

    async def list_loss(
        self,
        *,
        limit: int,
        cursor: str | None,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
    ) -> PageData[AdsLossView]:
        start, end = _read_window(date_from, date_to)
        before_date, before_id = _date_cursor(cursor)
        rows = await self.metrics.list_loss(
            limit=limit,
            before_date=before_date,
            before_id=before_id,
            date_from=start,
            date_to=end,
            shop_id=shop_id,
            platform_code=self._platform(platform_code),
        )
        visible_rows, next_cursor, has_more = _loss_page(rows, limit)
        items = [_loss_view(metric, campaign) for metric, campaign in visible_rows]
        return PageData[AdsLossView](items=items, page_info=PageInfo(cursor=next_cursor, has_more=has_more))

    async def sku_profit(
        self,
        *,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
    ) -> list[AdsSkuProfitView]:
        start, end = _read_window(date_from, date_to)
        spend_rows = await self.metrics.spend_by_sku(
            date_from=start,
            date_to=end,
            shop_id=shop_id,
            platform_code=self._platform(platform_code),
        )
        profits = await self.profits.list_between(
            date_from=start,
            date_to=end,
            sku_id=None,
            shop_id=shop_id,
            currency=None,
        )
        grouped: dict[tuple[int, int, str], list[SkuProfitDaily]] = {}
        for row in profits:
            grouped.setdefault((int(row.sku_id), int(row.shop_id), row.currency), []).append(row)
        views: list[AdsSkuProfitView] = []
        for sku_id, row_shop_id, currency, spend in spend_rows:
            if sku_id is None:
                views.append(
                    AdsSkuProfitView(
                        sku_id=None,
                        currency=currency,
                        actual_spend=money_to_str(quantize(spend)) or "0.000000",
                        formula="广告没有关联 SKU，不能并入净利。",
                        complete=False,
                    )
                )
                continue
            bucket = grouped.get((sku_id, row_shop_id, currency), [])
            nets = [row.net_profit for row in bucket]
            estimates = [line_amount(list(row.lines or []), "ADS") for row in bucket]
            value, formula, complete = combine_sku_ads(
                net_profits=nets,
                estimated_ads=estimates,
                actual_spend=spend,
            )
            estimated_total = _sum_decimals(estimates) if complete else None
            net_total = _sum_decimals(nets) if complete else None
            views.append(
                AdsSkuProfitView(
                    sku_id=str(sku_id),
                    currency=currency,
                    actual_spend=money_to_str(quantize(spend)) or "0.000000",
                    estimated_ads=money_to_str(estimated_total),
                    net_profit=money_to_str(net_total),
                    net_after_ads=money_to_str(value),
                    formula=formula,
                    complete=complete,
                )
            )
        return views

    async def _pull(
        self,
        shop: Shop,
        cred: CredentialView,
        start: date,
        end: date,
    ) -> list[UnifiedAdCampaign]:
        adapter = adapter_registry.get(shop.platform_code)
        since = datetime.combine(start, time.min, tzinfo=UTC)
        until = datetime.combine(end, time.max, tzinfo=UTC)
        items: list[UnifiedAdCampaign] = []
        cursor: str | None = None
        seen: set[str] = set()
        try:
            for _page in range(settings.ads_sync_max_pages):
                page = await adapter.fetch_ads(cred, since=since, until=until, cursor=cursor)
                items.extend(page.items)
                if not page.next_cursor or page.next_cursor in seen:
                    break
                seen.add(page.next_cursor)
                cursor = page.next_cursor
        except AdapterError as exc:
            raise AdsReportInvalidError(str(exc)) from exc
        return items

    async def _store(
        self,
        shop: Shop,
        pulled: list[UnifiedAdCampaign],
        start: date,
        end: date,
        actor_id: int | None,
    ) -> tuple[int, int, int, int]:
        campaigns = 0
        days = 0
        keywords = 0
        losses = 0
        for item in pulled:
            sku_id = None
            if item.platform_sku_id:
                sku_id = await self.listings.linked_sku_id(shop.id, item.platform_sku_id)
            campaign = await self._upsert_campaign(shop, item, sku_id, actor_id)
            campaigns += 1
            for day in item.days:
                if day.stat_date < start or day.stat_date > end:
                    continue
                margin = await self._margin(sku_id, shop.id, day.stat_date, day.currency)
                planned = plan_day(
                    impressions=day.impressions,
                    clicks=day.clicks,
                    orders=day.orders,
                    spend=day.spend,
                    sales=day.sales,
                    gross_margin=margin,
                )
                await self._upsert_day(campaign, shop.id, day.stat_date, day.currency, planned)
                days += 1
                if planned.loss_flag:
                    losses += 1
                for keyword in day.keywords:
                    await self._upsert_keyword(campaign, shop.id, day.stat_date, day.currency, keyword)
                    keywords += 1
        return campaigns, days, keywords, losses

    async def _margin(self, sku_id: int | None, shop_id: int, stat_date: date, currency: str) -> Decimal | None:
        if sku_id is None:
            return None
        row = await self.profits.get_key(sku_id, shop_id, stat_date, currency)
        if row is None:
            return None
        return gross_margin_ratio(Decimal(row.revenue), list(row.lines or []))

    async def _upsert_campaign(
        self,
        shop: Shop,
        item: UnifiedAdCampaign,
        sku_id: int | None,
        actor_id: int | None,
    ) -> AdCampaign:
        current = await self.campaigns.get_platform(shop.id, item.platform_campaign_id)
        if current is None:
            return await self.campaigns.add(
                AdCampaign(
                    tenant_id=shop.tenant_id,
                    shop_id=shop.id,
                    platform_code=shop.platform_code,
                    platform_campaign_id=item.platform_campaign_id,
                    name=item.name,
                    campaign_type=item.campaign_type,
                    status=item.status,
                    currency=item.currency,
                    sku_id=sku_id,
                    platform_sku_id=item.platform_sku_id or "",
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
        current.name = item.name
        current.campaign_type = item.campaign_type
        current.status = item.status
        current.currency = item.currency
        current.sku_id = sku_id
        current.platform_sku_id = item.platform_sku_id or ""
        current.updated_by = actor_id
        await self.session.flush()
        return current

    async def _upsert_day(
        self,
        campaign: AdCampaign,
        shop_id: int,
        stat_date: date,
        currency: str,
        planned: DayMetrics,
    ) -> None:
        row = await self.metrics.get_day(campaign.id, stat_date)
        values = {
            "impressions": planned.impressions,
            "clicks": planned.clicks,
            "orders": planned.orders,
            "spend": planned.spend,
            "sales": planned.sales,
            "currency": currency,
            "ctr": planned.ctr,
            "acos": planned.acos,
            "roas": planned.roas,
            "cvr": planned.cvr,
            "gross_margin": planned.gross_margin,
            "loss_flag": planned.loss_flag,
            "suggestion_code": planned.suggestion_code,
        }
        if row is None:
            await self.metrics.add(
                AdMetricDaily(
                    tenant_id=campaign.tenant_id,
                    stat_date=stat_date,
                    campaign_id=campaign.id,
                    shop_id=shop_id,
                    **values,
                )
            )
            return
        for name, value in values.items():
            setattr(row, name, value)
        row.updated_at = datetime.now(UTC)
        await self.session.flush()

    async def _upsert_keyword(
        self,
        campaign: AdCampaign,
        shop_id: int,
        stat_date: date,
        currency: str,
        keyword: UnifiedAdKeyword,
    ) -> None:
        flagged = suggest_negative(spend=keyword.spend, orders=keyword.orders)
        row = await self.keywords.get_row(campaign.id, keyword.keyword, stat_date)
        if row is None:
            await self.keywords.add(
                AdKeywordMetric(
                    tenant_id=campaign.tenant_id,
                    stat_date=stat_date,
                    campaign_id=campaign.id,
                    shop_id=shop_id,
                    keyword=keyword.keyword,
                    impressions=keyword.impressions,
                    clicks=keyword.clicks,
                    orders=keyword.orders,
                    spend=keyword.spend,
                    sales=keyword.sales,
                    currency=currency,
                    suggest_negative=flagged,
                )
            )
            return
        row.impressions = keyword.impressions
        row.clicks = keyword.clicks
        row.orders = keyword.orders
        row.spend = keyword.spend
        row.sales = keyword.sales
        row.currency = currency
        row.suggest_negative = flagged
        row.updated_at = datetime.now(UTC)
        await self.session.flush()

    def _platform(self, platform_code: str | None) -> str | None:
        if platform_code is None or not platform_code.strip():
            return None
        if not adapter_registry.supports(platform_code):
            raise PlatformUnsupportedError(f"平台 {platform_code} 尚未接入")
        return platform_code.strip().lower()

    def _campaign_view(
        self,
        campaign: AdCampaign,
        totals: tuple[int, int, int, Decimal, Decimal, bool] | None,
        *,
        visible: bool,
    ) -> AdsCampaignView:
        impressions, clicks, orders, spend, sales, loss = totals or (0, 0, 0, _ZERO, _ZERO, False)
        planned = plan_day(
            impressions=impressions,
            clicks=clicks,
            orders=orders,
            spend=spend,
            sales=sales,
            gross_margin=None,
        )
        return AdsCampaignView(
            id=str(campaign.id),
            shop_id=str(campaign.shop_id),
            platform_code=campaign.platform_code,
            platform_campaign_id=campaign.platform_campaign_id,
            name=campaign.name,
            campaign_type=campaign.campaign_type,
            status=campaign.status,
            currency=campaign.currency,
            sku_id=shown_id(campaign.sku_id),
            impressions=impressions,
            clicks=clicks,
            orders=orders,
            ctr=shown_money(as_percent(planned.ctr), visible=True),
            cvr=shown_money(as_percent(planned.cvr), visible=True),
            spend=shown_money(planned.spend, visible=visible),
            sales=shown_money(planned.sales, visible=visible),
            acos=shown_money(as_percent(planned.acos), visible=visible),
            roas=shown_money(planned.roas, visible=visible),
            loss_flag=bool(loss) and visible,
        )


def _day_view(row: AdMetricDaily, *, visible: bool) -> AdsDayView:
    return AdsDayView(
        stat_date=row.stat_date,
        impressions=row.impressions,
        clicks=row.clicks,
        orders=row.orders,
        ctr=shown_money(as_percent(row.ctr), visible=True),
        cvr=shown_money(as_percent(row.cvr), visible=True),
        spend=shown_money(row.spend, visible=visible),
        sales=shown_money(row.sales, visible=visible),
        acos=shown_money(as_percent(row.acos), visible=visible),
        roas=shown_money(row.roas, visible=visible),
        gross_margin=shown_money(as_percent(row.gross_margin), visible=visible),
        loss_flag=bool(row.loss_flag) if visible else False,
        suggestion_code=row.suggestion_code if visible else "",
    )


def _keyword_view(metric: AdKeywordMetric, campaign: AdCampaign, *, visible: bool) -> AdsKeywordView:
    return AdsKeywordView(
        id=str(metric.id),
        campaign_id=str(campaign.id),
        campaign_name=campaign.name,
        platform_code=campaign.platform_code,
        stat_date=metric.stat_date,
        keyword=metric.keyword,
        impressions=metric.impressions,
        clicks=metric.clicks,
        orders=metric.orders,
        currency=metric.currency,
        spend=shown_money(metric.spend, visible=visible),
        sales=shown_money(metric.sales, visible=visible),
        suggest_negative=metric.suggest_negative,
    )


def _loss_view(metric: AdMetricDaily, campaign: AdCampaign) -> AdsLossView:
    return AdsLossView(
        id=str(metric.id),
        campaign_id=str(campaign.id),
        campaign_name=campaign.name,
        platform_code=campaign.platform_code,
        shop_id=str(campaign.shop_id),
        stat_date=metric.stat_date,
        currency=metric.currency,
        spend=money_to_str(metric.spend) or "0.000000",
        sales=money_to_str(metric.sales) or "0.000000",
        acos=shown_money(as_percent(metric.acos), visible=True),
        gross_margin=shown_money(as_percent(metric.gross_margin), visible=True),
        suggestion_code=metric.suggestion_code,
    )


def _sync_window(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    end = date_to or datetime.now(UTC).date()
    start = date_from or (end - timedelta(days=max(settings.ads_sync_lookback_days, 1) - 1))
    return _read_window(start, end)


def _read_window(date_from: date, date_to: date) -> tuple[date, date]:
    if date_from > date_to:
        raise ParamInvalidError("开始日期不能晚于结束日期")
    if (date_to - date_from).days + 1 > settings.ads_sync_max_days:
        raise ParamInvalidError("日期区间超过配置上限")
    return date_from, date_to


def _sum_decimals(values: list[Decimal | None]) -> Decimal:
    total = Decimal("0")
    for item in values:
        if item is None:
            continue
        total += item
    return quantize(total)


def _clean_key(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 128:
        raise ParamInvalidError("幂等键过长")
    return cleaned


def _before_id(cursor: str | None) -> int | None:
    if not cursor:
        return None
    raw = decode_cursor(cursor).get("id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _spend_cursor(cursor: str | None) -> tuple[Decimal | None, int | None]:
    if not cursor:
        return None, None
    payload = decode_cursor(cursor)
    try:
        spend = Decimal(str(payload["spend"]))
        row_id = int(payload["id"])
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None, None
    return spend, row_id


def _date_cursor(cursor: str | None) -> tuple[date | None, int | None]:
    if not cursor:
        return None, None
    payload = decode_cursor(cursor)
    try:
        stat_date = date.fromisoformat(str(payload["stat_date"]))
        row_id = int(payload["id"])
    except (KeyError, TypeError, ValueError):
        return None, None
    return stat_date, row_id


def _spend_page(
    rows: list[tuple[AdKeywordMetric, AdCampaign]],
    limit: int,
) -> tuple[list[tuple[AdKeywordMetric, AdCampaign]], str | None, bool]:
    has_more = len(rows) > limit
    visible = rows[:limit]
    cursor = None
    if has_more and visible:
        last = visible[-1][0]
        cursor = encode_cursor({"spend": money_to_str(last.spend), "id": str(last.id)})
    return visible, cursor, has_more


def _loss_page(
    rows: list[tuple[AdMetricDaily, AdCampaign]],
    limit: int,
) -> tuple[list[tuple[AdMetricDaily, AdCampaign]], str | None, bool]:
    has_more = len(rows) > limit
    visible = rows[:limit]
    cursor = None
    if has_more and visible:
        last = visible[-1][0]
        cursor = encode_cursor({"stat_date": last.stat_date.isoformat(), "id": str(last.id)})
    return visible, cursor, has_more
