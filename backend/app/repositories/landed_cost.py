"""落地成本费用与计算留痕。SQL 只留在这一层。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.landed_cost import FEE_ACTIVE, LandedCostCalc, LandedCostFee, LandedCostLineToggle
from app.repositories.base import BaseRepository


class LandedCostFeeRepository(BaseRepository[LandedCostFee]):
    model = LandedCostFee

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_for_key(self, market: str, channel: str, fee_code: str, label: str) -> list[LandedCostFee]:
        stmt = (
            self.base_select()
            .where(
                LandedCostFee.market == market,
                LandedCostFee.channel == channel,
                LandedCostFee.fee_code == fee_code,
                LandedCostFee.label == label,
            )
            .order_by(LandedCostFee.version.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_visible(self, *, market: str | None, limit: int) -> list[LandedCostFee]:
        stmt = self.base_select()
        if market is not None:
            stmt = stmt.where(LandedCostFee.market == market)
        stmt = stmt.order_by(
            LandedCostFee.market.asc(),
            LandedCostFee.channel.asc(),
            LandedCostFee.fee_code.asc(),
            LandedCostFee.label.asc(),
            LandedCostFee.version.desc(),
        ).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_effective(self, market: str, on: date) -> list[LandedCostFee]:
        stmt = self.base_select().where(
            LandedCostFee.market == market,
            LandedCostFee.status == FEE_ACTIVE,
            LandedCostFee.effective_from <= on,
            or_(LandedCostFee.effective_to.is_(None), LandedCostFee.effective_to > on),
        )
        return list((await self.session.execute(stmt)).scalars().all())


class LandedCostToggleRepository(BaseRepository[LandedCostLineToggle]):
    model = LandedCostLineToggle

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_visible(self, *, market: str | None, limit: int) -> list[LandedCostLineToggle]:
        stmt = self.base_select()
        if market is not None:
            stmt = stmt.where(LandedCostLineToggle.market == market)
        stmt = stmt.order_by(
            LandedCostLineToggle.market.asc(),
            LandedCostLineToggle.channel.asc(),
            LandedCostLineToggle.line_code.asc(),
        ).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_effective(self, market: str, channel: str) -> list[LandedCostLineToggle]:
        stmt = self.base_select().where(
            LandedCostLineToggle.market == market,
            LandedCostLineToggle.channel.in_((channel, "*")),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_key(self, market: str, channel: str, line_code: str) -> LandedCostLineToggle | None:
        stmt = self.base_select().where(
            LandedCostLineToggle.market == market,
            LandedCostLineToggle.channel == channel,
            LandedCostLineToggle.line_code == line_code,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class LandedCostCalcRepository(BaseRepository[LandedCostCalc]):
    model = LandedCostCalc

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_key(self, key: str) -> LandedCostCalc | None:
        stmt = self.base_select().where(LandedCostCalc.idempotency_key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()
