"""汇率与日利润。SQL 只留在这一层。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import ExchangeRate, Settlement, SettlementItem, SkuProfitDaily
from app.models.hs_code import HsCode, SpuHsBinding
from app.models.order import OrderItem, SalesOrder
from app.models.product import Sku
from app.repositories.base import BaseRepository


@dataclass(frozen=True)
class ProfitSourceLine:
    sku_id: int | None
    shop_id: int
    stat_date: date
    quantity: int
    unit_price: Decimal
    currency: str
    spu_id: int | None
    purchase_price: Decimal | None
    purchase_currency: str | None
    weight_g: Decimal | None
    length_cm: Decimal | None
    width_cm: Decimal | None
    height_cm: Decimal | None


@dataclass(frozen=True)
class SkuCostFact:
    spu_id: int
    purchase_price: Decimal | None
    purchase_currency: str | None
    weight_g: Decimal
    length_cm: Decimal
    width_cm: Decimal
    height_cm: Decimal


class ExchangeRateRepository(BaseRepository[ExchangeRate]):
    model = ExchangeRate

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_key(self, key: str) -> ExchangeRate | None:
        stmt = self.base_select().where(ExchangeRate.idempotency_key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_exact(
        self,
        base_currency: str,
        quote_currency: str,
        basis: str,
        effective_on: date,
    ) -> ExchangeRate | None:
        stmt = self.base_select().where(
            ExchangeRate.base_currency == base_currency,
            ExchangeRate.quote_currency == quote_currency,
            ExchangeRate.basis == basis,
            ExchangeRate.effective_on == effective_on,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def latest(self, base_currency: str, quote_currency: str, basis: str, on: date) -> ExchangeRate | None:
        stmt = (
            self.base_select()
            .where(
                ExchangeRate.base_currency == base_currency,
                ExchangeRate.quote_currency == quote_currency,
                ExchangeRate.basis == basis,
                ExchangeRate.effective_on <= on,
            )
            .order_by(ExchangeRate.effective_on.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_visible(
        self,
        *,
        basis: str | None,
        base_currency: str | None,
        quote_currency: str | None,
        limit: int,
    ) -> list[ExchangeRate]:
        stmt = self.base_select()
        if basis is not None:
            stmt = stmt.where(ExchangeRate.basis == basis)
        if base_currency is not None:
            stmt = stmt.where(ExchangeRate.base_currency == base_currency)
        if quote_currency is not None:
            stmt = stmt.where(ExchangeRate.quote_currency == quote_currency)
        stmt = stmt.order_by(ExchangeRate.effective_on.desc(), ExchangeRate.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())


class SkuProfitRepository(BaseRepository[SkuProfitDaily]):
    model = SkuProfitDaily

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_key(self, sku_id: int, shop_id: int, stat_date: date, currency: str) -> SkuProfitDaily | None:
        stmt = self.base_select().where(
            SkuProfitDaily.sku_id == sku_id,
            SkuProfitDaily.shop_id == shop_id,
            SkuProfitDaily.stat_date == stat_date,
            SkuProfitDaily.currency == currency,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_between(
        self,
        *,
        date_from: date,
        date_to: date,
        sku_id: int | None,
        shop_id: int | None,
        currency: str | None,
    ) -> list[SkuProfitDaily]:
        stmt = self.base_select().where(
            SkuProfitDaily.stat_date >= date_from,
            SkuProfitDaily.stat_date <= date_to,
        )
        if sku_id is not None:
            stmt = stmt.where(SkuProfitDaily.sku_id == sku_id)
        if shop_id is not None:
            stmt = stmt.where(SkuProfitDaily.shop_id == shop_id)
        if currency is not None:
            stmt = stmt.where(SkuProfitDaily.currency == currency)
        stmt = stmt.order_by(SkuProfitDaily.stat_date.asc(), SkuProfitDaily.sku_id.asc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def order_lines(self, date_from: date, date_to: date) -> list[ProfitSourceLine]:
        occurred = func.coalesce(SalesOrder.paid_at, SalesOrder.created_at)
        stat = cast(func.timezone("UTC", occurred), Date)
        stmt = (
            select(
                OrderItem.sku_id,
                SalesOrder.shop_id,
                stat,
                OrderItem.quantity,
                OrderItem.unit_price,
                OrderItem.currency,
            )
            .join(SalesOrder, SalesOrder.id == OrderItem.order_id)
            .where(stat >= date_from, stat <= date_to, OrderItem.quantity > 0)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            ProfitSourceLine(
                sku_id=row[0],
                shop_id=int(row[1]),
                stat_date=row[2],
                quantity=int(row[3]),
                unit_price=row[4],
                currency=str(row[5]).strip(),
                spu_id=None,
                purchase_price=None,
                purchase_currency=None,
                weight_g=None,
                length_cm=None,
                width_cm=None,
                height_cm=None,
            )
            for row in rows
        ]

    async def sku_costs(self, sku_ids: list[int]) -> dict[int, SkuCostFact]:
        if not sku_ids:
            return {}
        stmt = select(
            Sku.id,
            Sku.spu_id,
            Sku.purchase_price,
            Sku.currency,
            Sku.weight_g,
            Sku.length_cm,
            Sku.width_cm,
            Sku.height_cm,
        ).where(Sku.id.in_(sku_ids))
        rows = (await self.session.execute(stmt)).all()
        return {
            int(row[0]): SkuCostFact(
                spu_id=int(row[1]),
                purchase_price=row[2],
                purchase_currency=str(row[3]).strip() if row[3] else None,
                weight_g=row[4],
                length_cm=row[5],
                width_cm=row[6],
                height_cm=row[7],
            )
            for row in rows
        }

    async def hs_codes(self, spu_ids: list[int], market: str) -> dict[int, str]:
        if not spu_ids:
            return {}
        stmt = (
            select(SpuHsBinding.spu_id, HsCode.code)
            .join(HsCode, HsCode.id == SpuHsBinding.hs_code_id)
            .where(SpuHsBinding.spu_id.in_(spu_ids), SpuHsBinding.market == market)
        )
        rows = (await self.session.execute(stmt)).all()
        return {int(spu_id): str(code) for spu_id, code in rows}


class SettlementRepository(BaseRepository[Settlement]):
    model = Settlement

    async def get_by_key(self, key: str) -> Settlement | None:
        return await self.get_by(idempotency_key=key)

    async def get_natural(self, shop_id: int, platform_settlement_id: str) -> Settlement | None:
        return await self.get_by(shop_id=shop_id, platform_settlement_id=platform_settlement_id)

    async def list_visible(self, *, limit: int) -> list[Settlement]:
        stmt = self.base_select().order_by(Settlement.period_end.desc(), Settlement.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def items_of(self, settlement_id: int) -> list[SettlementItem]:
        stmt = (
            select(SettlementItem)
            .where(SettlementItem.settlement_id == settlement_id)
            .order_by(SettlementItem.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def orders_for(self, shop_id: int, platform_order_ids: list[str]) -> list[SalesOrder]:
        if not platform_order_ids:
            return []
        stmt = select(SalesOrder).where(
            SalesOrder.shop_id == shop_id,
            SalesOrder.platform_order_id.in_(platform_order_ids),
        )
        return list((await self.session.execute(stmt)).scalars().all())
