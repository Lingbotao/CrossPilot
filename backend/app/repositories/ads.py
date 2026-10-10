"""广告域查询。SQL 只留在这一层。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Select

from app.models.ads import AdCampaign, AdKeywordMetric, AdMetricDaily
from app.repositories.base import BaseRepository


class AdCampaignRepository(BaseRepository[AdCampaign]):
    model = AdCampaign

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_platform(self, shop_id: int, platform_campaign_id: str) -> AdCampaign | None:
        stmt = self.base_select().where(
            AdCampaign.shop_id == shop_id,
            AdCampaign.platform_campaign_id == platform_campaign_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_cursor(
        self,
        *,
        limit: int,
        before_id: int | None,
        shop_id: int | None,
        platform_code: str | None,
        campaign_id: int | None,
    ) -> list[AdCampaign]:
        stmt = self.base_select()
        if before_id is not None:
            stmt = stmt.where(AdCampaign.id < before_id)
        if shop_id is not None:
            stmt = stmt.where(AdCampaign.shop_id == shop_id)
        if platform_code is not None:
            stmt = stmt.where(AdCampaign.platform_code == platform_code)
        if campaign_id is not None:
            stmt = stmt.where(AdCampaign.id == campaign_id)
        stmt = stmt.order_by(AdCampaign.id.desc()).limit(limit + 1)
        return list((await self.session.execute(stmt)).scalars().all())


class AdMetricRepository(BaseRepository[AdMetricDaily]):
    model = AdMetricDaily

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_day(self, campaign_id: int, stat_date: date) -> AdMetricDaily | None:
        stmt = self.base_select().where(
            AdMetricDaily.campaign_id == campaign_id,
            AdMetricDaily.stat_date == stat_date,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_days(self, campaign_id: int, date_from: date, date_to: date) -> list[AdMetricDaily]:
        stmt = (
            self.base_select()
            .where(
                AdMetricDaily.campaign_id == campaign_id,
                AdMetricDaily.stat_date >= date_from,
                AdMetricDaily.stat_date <= date_to,
            )
            .order_by(AdMetricDaily.stat_date.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def sums_for(
        self, campaign_ids: list[int], date_from: date, date_to: date
    ) -> dict[int, tuple[int, int, int, Decimal, Decimal, bool]]:
        if not campaign_ids:
            return {}
        stmt = (
            select(
                AdMetricDaily.campaign_id,
                func.coalesce(func.sum(AdMetricDaily.impressions), 0),
                func.coalesce(func.sum(AdMetricDaily.clicks), 0),
                func.coalesce(func.sum(AdMetricDaily.orders), 0),
                func.coalesce(func.sum(AdMetricDaily.spend), 0),
                func.coalesce(func.sum(AdMetricDaily.sales), 0),
                func.coalesce(func.bool_or(AdMetricDaily.loss_flag), False),
            )
            .where(
                AdMetricDaily.campaign_id.in_(campaign_ids),
                AdMetricDaily.stat_date >= date_from,
                AdMetricDaily.stat_date <= date_to,
            )
            .group_by(AdMetricDaily.campaign_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {
            int(campaign_id): (
                int(impressions),
                int(clicks),
                int(orders),
                Decimal(spend),
                Decimal(sales),
                bool(loss),
            )
            for campaign_id, impressions, clicks, orders, spend, sales, loss in rows
        }

    async def activity(
        self,
        *,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
        campaign_id: int | None,
    ) -> tuple[int, int, int]:
        stmt = (
            select(
                func.coalesce(func.sum(AdMetricDaily.impressions), 0),
                func.coalesce(func.sum(AdMetricDaily.clicks), 0),
                func.coalesce(func.sum(AdMetricDaily.orders), 0),
            )
            .join(AdCampaign, AdCampaign.id == AdMetricDaily.campaign_id)
            .where(AdMetricDaily.stat_date >= date_from, AdMetricDaily.stat_date <= date_to)
        )
        stmt = _scope(stmt, shop_id=shop_id, platform_code=platform_code, campaign_id=campaign_id)
        impressions, clicks, orders = (await self.session.execute(stmt)).one()
        return int(impressions), int(clicks), int(orders)

    async def money_by_currency(
        self,
        *,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
        campaign_id: int | None,
    ) -> list[tuple[str, Decimal, Decimal]]:
        stmt = (
            select(
                AdMetricDaily.currency,
                func.coalesce(func.sum(AdMetricDaily.spend), 0),
                func.coalesce(func.sum(AdMetricDaily.sales), 0),
            )
            .join(AdCampaign, AdCampaign.id == AdMetricDaily.campaign_id)
            .where(AdMetricDaily.stat_date >= date_from, AdMetricDaily.stat_date <= date_to)
            .group_by(AdMetricDaily.currency)
            .order_by(AdMetricDaily.currency.asc())
        )
        stmt = _scope(stmt, shop_id=shop_id, platform_code=platform_code, campaign_id=campaign_id)
        rows = (await self.session.execute(stmt)).all()
        return [(str(currency), Decimal(spend), Decimal(sales)) for currency, spend, sales in rows]

    async def list_loss(
        self,
        *,
        limit: int,
        before_date: date | None,
        before_id: int | None,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
    ) -> list[tuple[AdMetricDaily, AdCampaign]]:
        stmt = (
            select(AdMetricDaily, AdCampaign)
            .join(AdCampaign, AdCampaign.id == AdMetricDaily.campaign_id)
            .where(
                AdMetricDaily.loss_flag.is_(True),
                AdMetricDaily.stat_date >= date_from,
                AdMetricDaily.stat_date <= date_to,
            )
        )
        if before_date is not None and before_id is not None:
            stmt = stmt.where(
                or_(
                    AdMetricDaily.stat_date < before_date,
                    and_(AdMetricDaily.stat_date == before_date, AdMetricDaily.id < before_id),
                )
            )
        stmt = _scope(stmt, shop_id=shop_id, platform_code=platform_code, campaign_id=None)
        stmt = stmt.order_by(AdMetricDaily.stat_date.desc(), AdMetricDaily.id.desc()).limit(limit + 1)
        rows = (await self.session.execute(stmt)).all()
        return [(cast(AdMetricDaily, row[0]), cast(AdCampaign, row[1])) for row in rows]

    async def spend_by_sku(
        self,
        *,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
    ) -> list[tuple[int | None, int, str, Decimal]]:
        stmt = (
            select(
                AdCampaign.sku_id,
                AdCampaign.shop_id,
                AdMetricDaily.currency,
                func.coalesce(func.sum(AdMetricDaily.spend), 0),
            )
            .join(AdCampaign, AdCampaign.id == AdMetricDaily.campaign_id)
            .where(AdMetricDaily.stat_date >= date_from, AdMetricDaily.stat_date <= date_to)
            .group_by(AdCampaign.sku_id, AdCampaign.shop_id, AdMetricDaily.currency)
            .order_by(AdCampaign.sku_id.asc().nulls_last(), AdMetricDaily.currency.asc())
        )
        stmt = _scope(stmt, shop_id=shop_id, platform_code=platform_code, campaign_id=None)
        rows = (await self.session.execute(stmt)).all()
        return [
            (None if sku_id is None else int(sku_id), int(shop), str(currency), Decimal(spend))
            for sku_id, shop, currency, spend in rows
        ]


class AdKeywordRepository(BaseRepository[AdKeywordMetric]):
    model = AdKeywordMetric

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_row(self, campaign_id: int, keyword: str, stat_date: date) -> AdKeywordMetric | None:
        stmt = self.base_select().where(
            AdKeywordMetric.campaign_id == campaign_id,
            AdKeywordMetric.keyword == keyword,
            AdKeywordMetric.stat_date == stat_date,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_cursor(
        self,
        *,
        limit: int,
        before_spend: Decimal | None,
        before_id: int | None,
        date_from: date,
        date_to: date,
        shop_id: int | None,
        platform_code: str | None,
        campaign_id: int | None,
        only_negative: bool,
    ) -> list[tuple[AdKeywordMetric, AdCampaign]]:
        stmt = (
            select(AdKeywordMetric, AdCampaign)
            .join(AdCampaign, AdCampaign.id == AdKeywordMetric.campaign_id)
            .where(AdKeywordMetric.stat_date >= date_from, AdKeywordMetric.stat_date <= date_to)
        )
        if only_negative:
            stmt = stmt.where(AdKeywordMetric.suggest_negative.is_(True))
        if before_spend is not None and before_id is not None:
            stmt = stmt.where(
                or_(
                    AdKeywordMetric.spend < before_spend,
                    and_(AdKeywordMetric.spend == before_spend, AdKeywordMetric.id < before_id),
                )
            )
        stmt = _scope(stmt, shop_id=shop_id, platform_code=platform_code, campaign_id=campaign_id)
        stmt = stmt.order_by(AdKeywordMetric.spend.desc(), AdKeywordMetric.id.desc()).limit(limit + 1)
        rows = (await self.session.execute(stmt)).all()
        return [(cast(AdKeywordMetric, row[0]), cast(AdCampaign, row[1])) for row in rows]


def _scope(
    stmt: Select[Any],
    *,
    shop_id: int | None,
    platform_code: str | None,
    campaign_id: int | None,
) -> Select[Any]:
    if shop_id is not None:
        stmt = stmt.where(AdCampaign.shop_id == shop_id)
    if platform_code is not None:
        stmt = stmt.where(AdCampaign.platform_code == platform_code)
    if campaign_id is not None:
        stmt = stmt.where(AdCampaign.id == campaign_id)
    return stmt
