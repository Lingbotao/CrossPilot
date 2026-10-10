"""看板汇总的读写。业务口径在 engines/dashboard.py，这里只取数和替换区间。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, and_, case, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.core.context import get_tenant_id
from app.core.errors import CrossTenantError
from app.db.snowflake import next_snowflake_id
from app.engines.dashboard import (
    GMV_STATUSES,
    AdFact,
    CostLineTotal,
    OrderFact,
    ProfitRow,
    ReturnFact,
    ShopDay,
    SkuStock,
)
from app.engines.landed_cost import money_text
from app.models.ads import AdMetricDaily
from app.models.dashboard import DashboardInventoryDaily, DashboardShopDaily
from app.models.finance import SkuProfitDaily
from app.models.inventory import Inventory
from app.models.order import OrderItem, ReturnOrder, SalesOrder
from app.models.platform import Shop
from app.models.product import Sku


def _tenant_id() -> int:
    current = get_tenant_id()
    if current is None:
        raise CrossTenantError("缺少租户上下文，拒绝写入")
    return int(current)


def _stat_date(column: Any) -> ColumnElement[date]:
    return cast(func.timezone("UTC", column), Date)


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def shops(self) -> dict[int, tuple[str, str]]:
        stmt = select(Shop.id, Shop.platform_code, Shop.site_code).where(Shop.deleted_at.is_(None))
        rows = (await self.session.execute(stmt)).all()
        return {int(row[0]): (str(row[1]), str(row[2])) for row in rows}

    async def order_facts(self, start: date, end: date, *, sla_hours: int) -> list[OrderFact]:
        stat = _stat_date(SalesOrder.paid_at)
        deadline = SalesOrder.paid_at + timedelta(hours=sla_hours)
        counted = SalesOrder.unified_status.in_(GMV_STATUSES)
        on_time = and_(counted, SalesOrder.shipped_at.is_not(None), SalesOrder.shipped_at <= deadline)
        late = and_(
            counted,
            or_(
                and_(SalesOrder.shipped_at.is_not(None), SalesOrder.shipped_at > deadline),
                and_(
                    SalesOrder.shipped_at.is_(None),
                    SalesOrder.unified_status == "PAID",
                    deadline < func.now(),
                ),
            ),
        )
        stmt = (
            select(
                SalesOrder.shop_id,
                SalesOrder.platform_code,
                Shop.site_code,
                stat,
                SalesOrder.currency,
                func.count(),
                func.coalesce(func.sum(SalesOrder.total_amount), 0),
                func.coalesce(func.sum(case((on_time, 1), else_=0)), 0),
                func.coalesce(func.sum(case((late, 1), else_=0)), 0),
            )
            .join(Shop, Shop.id == SalesOrder.shop_id)
            .where(SalesOrder.paid_at.is_not(None), counted, stat >= start, stat <= end)
            .group_by(SalesOrder.shop_id, SalesOrder.platform_code, Shop.site_code, stat, SalesOrder.currency)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            OrderFact(
                shop_id=int(row[0]),
                platform_code=str(row[1]),
                site_code=str(row[2]),
                stat_date=row[3],
                currency=str(row[4]).strip(),
                order_count=int(row[5]),
                gmv=Decimal(row[6]),
                on_time=int(row[7]),
                late=int(row[8]),
            )
            for row in rows
        ]

    async def return_facts(self, start: date, end: date) -> list[ReturnFact]:
        stat = _stat_date(ReturnOrder.created_at)
        stmt = (
            select(SalesOrder.shop_id, stat, SalesOrder.currency, func.count())
            .join(SalesOrder, SalesOrder.id == ReturnOrder.order_id)
            .where(stat >= start, stat <= end)
            .group_by(SalesOrder.shop_id, stat, SalesOrder.currency)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            ReturnFact(shop_id=int(row[0]), stat_date=row[1], currency=str(row[2]).strip(), count=int(row[3]))
            for row in rows
        ]

    async def ad_facts(self, start: date, end: date) -> list[AdFact]:
        stmt = (
            select(
                AdMetricDaily.shop_id,
                AdMetricDaily.stat_date,
                AdMetricDaily.currency,
                func.coalesce(func.sum(AdMetricDaily.spend), 0),
                func.coalesce(func.sum(AdMetricDaily.sales), 0),
                func.coalesce(func.sum(case((AdMetricDaily.loss_flag.is_(True), 1), else_=0)), 0),
            )
            .where(AdMetricDaily.stat_date >= start, AdMetricDaily.stat_date <= end)
            .group_by(AdMetricDaily.shop_id, AdMetricDaily.stat_date, AdMetricDaily.currency)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            AdFact(
                shop_id=int(row[0]),
                stat_date=row[1],
                currency=str(row[2]).strip(),
                spend=Decimal(row[3]),
                sales=Decimal(row[4]),
                loss_count=int(row[5]),
            )
            for row in rows
        ]

    async def profit_rows(self, start: date, end: date) -> list[ProfitRow]:
        stmt = select(
            SkuProfitDaily.shop_id,
            SkuProfitDaily.stat_date,
            SkuProfitDaily.currency,
            SkuProfitDaily.book_currency,
            SkuProfitDaily.revenue,
            SkuProfitDaily.book_revenue,
            SkuProfitDaily.net_profit,
            SkuProfitDaily.book_net_profit,
            SkuProfitDaily.complete,
            SkuProfitDaily.quantity,
            SkuProfitDaily.lines,
        ).where(SkuProfitDaily.stat_date >= start, SkuProfitDaily.stat_date <= end)
        rows = (await self.session.execute(stmt)).all()
        return [
            ProfitRow(
                shop_id=int(row[0]),
                stat_date=row[1],
                currency=str(row[2]).strip(),
                book_currency=str(row[3]).strip(),
                revenue=Decimal(row[4]),
                book_revenue=None if row[5] is None else Decimal(row[5]),
                net_profit=None if row[6] is None else Decimal(row[6]),
                book_net_profit=None if row[7] is None else Decimal(row[7]),
                complete=bool(row[8]),
                quantity=int(row[9]),
                lines=list(row[10] or []),
            )
            for row in rows
        ]

    async def replace_shop_days(self, start: date, end: date, days: list[ShopDay]) -> int:
        tenant_id = _tenant_id()
        await self.session.execute(
            delete(DashboardShopDaily).where(
                DashboardShopDaily.tenant_id == tenant_id,
                DashboardShopDaily.stat_date >= start,
                DashboardShopDaily.stat_date <= end,
            )
        )
        now = datetime.now(UTC)
        for day in days:
            self.session.add(
                DashboardShopDaily(
                    id=next_snowflake_id(),
                    tenant_id=tenant_id,
                    stat_date=day.stat_date,
                    created_at=now,
                    updated_at=now,
                    shop_id=day.shop_id,
                    platform_code=day.platform_code,
                    site_code=day.site_code,
                    currency=day.currency,
                    book_currency=day.book_currency,
                    order_count=day.order_count,
                    gmv=day.gmv,
                    book_gmv=day.book_gmv,
                    net_profit=day.net_profit,
                    book_net_profit=day.book_net_profit,
                    profit_complete=day.profit_complete,
                    on_time=day.on_time,
                    late=day.late,
                    return_count=day.return_count,
                    ad_spend=day.ad_spend,
                    ad_sales=day.ad_sales,
                    ad_loss_count=day.ad_loss_count,
                    lines=_dump_lines(day.lines),
                )
            )
        await self.session.flush()
        return len(days)

    async def list_shop_days(
        self,
        start: date,
        end: date,
        *,
        shop_id: int | None,
        platform_code: str | None,
    ) -> list[ShopDay]:
        stmt = select(DashboardShopDaily).where(
            DashboardShopDaily.stat_date >= start,
            DashboardShopDaily.stat_date <= end,
        )
        if shop_id is not None:
            stmt = stmt.where(DashboardShopDaily.shop_id == shop_id)
        if platform_code:
            stmt = stmt.where(DashboardShopDaily.platform_code == platform_code)
        stmt = stmt.order_by(DashboardShopDaily.stat_date.asc(), DashboardShopDaily.shop_id.asc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_to_day(row) for row in rows]

    async def sku_ranks(self, start: date, end: date, *, shop_id: int | None, platform_code: str | None) -> list[Any]:
        stmt = (
            select(
                SkuProfitDaily.sku_id,
                Sku.spu_id,
                Sku.sku_code,
                SkuProfitDaily.currency,
                SkuProfitDaily.book_currency,
                func.coalesce(func.sum(SkuProfitDaily.quantity), 0),
                func.coalesce(func.sum(SkuProfitDaily.revenue), 0),
                func.sum(SkuProfitDaily.net_profit),
                func.sum(SkuProfitDaily.book_net_profit),
                func.bool_and(SkuProfitDaily.complete),
            )
            .join(Sku, Sku.id == SkuProfitDaily.sku_id)
            .join(Shop, Shop.id == SkuProfitDaily.shop_id)
            .where(SkuProfitDaily.stat_date >= start, SkuProfitDaily.stat_date <= end, Sku.deleted_at.is_(None))
            .group_by(
                SkuProfitDaily.sku_id,
                Sku.spu_id,
                Sku.sku_code,
                SkuProfitDaily.currency,
                SkuProfitDaily.book_currency,
            )
        )
        if shop_id is not None:
            stmt = stmt.where(SkuProfitDaily.shop_id == shop_id)
        if platform_code:
            stmt = stmt.where(Shop.platform_code == platform_code)
        return list((await self.session.execute(stmt)).all())

    async def inventory_balances(self) -> list[SkuStock]:
        stmt = (
            select(
                Sku.id,
                Sku.currency,
                Sku.purchase_price,
                func.coalesce(func.sum(Inventory.available), 0),
                func.coalesce(func.max(Inventory.safe_stock), 0),
            )
            .join(Inventory, Inventory.sku_id == Sku.id)
            .where(Sku.deleted_at.is_(None))
            .group_by(Sku.id, Sku.currency, Sku.purchase_price)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            SkuStock(
                sku_id=int(row[0]),
                available=int(row[3]),
                safe_stock=int(row[4]),
                sold_qty=0,
                purchase_price=None if row[2] is None else Decimal(row[2]),
                currency=None if row[1] is None else str(row[1]).strip(),
            )
            for row in rows
        ]

    async def sold_by_sku(self, start: date, end: date) -> dict[int, int]:
        stat = _stat_date(SalesOrder.paid_at)
        stmt = (
            select(OrderItem.sku_id, func.coalesce(func.sum(OrderItem.quantity), 0))
            .join(SalesOrder, SalesOrder.id == OrderItem.order_id)
            .where(
                OrderItem.sku_id.is_not(None),
                SalesOrder.paid_at.is_not(None),
                SalesOrder.unified_status.in_(GMV_STATUSES),
                stat >= start,
                stat <= end,
            )
            .group_by(OrderItem.sku_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {int(row[0]): int(row[1]) for row in rows}

    async def sold_qty(self, start: date, end: date) -> int:
        counted = await self.sold_by_sku(start, end)
        return sum(counted.values())

    async def replace_inventory(
        self,
        stat_date: date,
        *,
        on_hand_qty: int,
        stockout_sku_count: int,
        below_safe_sku_count: int,
        stale_sku_count: int,
        stale_amounts: list[dict[str, object]],
    ) -> None:
        tenant_id = _tenant_id()
        await self.session.execute(
            delete(DashboardInventoryDaily).where(
                DashboardInventoryDaily.tenant_id == tenant_id,
                DashboardInventoryDaily.stat_date == stat_date,
            )
        )
        now = datetime.now(UTC)
        self.session.add(
            DashboardInventoryDaily(
                id=next_snowflake_id(),
                tenant_id=tenant_id,
                stat_date=stat_date,
                created_at=now,
                updated_at=now,
                on_hand_qty=on_hand_qty,
                stockout_sku_count=stockout_sku_count,
                below_safe_sku_count=below_safe_sku_count,
                stale_sku_count=stale_sku_count,
                stale_amounts=stale_amounts,
            )
        )
        await self.session.flush()

    async def latest_inventory(self, end: date) -> DashboardInventoryDaily | None:
        stmt = (
            select(DashboardInventoryDaily)
            .where(DashboardInventoryDaily.stat_date <= end)
            .order_by(DashboardInventoryDaily.stat_date.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


def _dump_lines(lines: list[CostLineTotal]) -> list[dict[str, object]]:
    return [
        {
            "code": line.code,
            "amount": None if line.amount is None else money_text(line.amount),
            "complete": line.complete,
        }
        for line in lines
    ]


def _to_day(row: DashboardShopDaily) -> ShopDay:
    payload = list(row.lines or [])
    lines = [
        CostLineTotal(
            code=str(item.get("code") or ""),
            amount=None if item.get("amount") is None else Decimal(str(item["amount"])),
            complete=bool(item.get("complete")),
        )
        for item in payload
        if isinstance(item, dict)
    ]
    return ShopDay(
        shop_id=int(row.shop_id),
        platform_code=row.platform_code,
        site_code=row.site_code,
        stat_date=row.stat_date,
        currency=str(row.currency).strip(),
        book_currency=str(row.book_currency).strip(),
        order_count=int(row.order_count),
        gmv=Decimal(row.gmv),
        book_gmv=None if row.book_gmv is None else Decimal(row.book_gmv),
        net_profit=None if row.net_profit is None else Decimal(row.net_profit),
        book_net_profit=None if row.book_net_profit is None else Decimal(row.book_net_profit),
        profit_complete=bool(row.profit_complete),
        on_time=int(row.on_time),
        late=int(row.late),
        return_count=int(row.return_count),
        ad_spend=None if row.ad_spend is None else Decimal(row.ad_spend),
        ad_sales=None if row.ad_sales is None else Decimal(row.ad_sales),
        ad_loss_count=int(row.ad_loss_count),
        lines=lines,
    )
