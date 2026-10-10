"""导入平台结算报表并匹配本租户订单。差额只展示，不回写日利润。"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, ParamInvalidError, SettlementFileInvalidError
from app.engines.landed_cost import quantize
from app.engines.settlement import (
    MATCHED,
    OrderGap,
    OrderRef,
    SettlementLine,
    header_amount,
    reconcile,
)
from app.models.enums import AuditAction
from app.models.finance import Settlement, SettlementItem
from app.models.order import SalesOrder
from app.repositories.finance import SettlementRepository
from app.repositories.identity import AuditLogRepository
from app.repositories.platform import ShopRepository
from app.schemas.common import money_to_str
from app.schemas.product import decimal_text
from app.schemas.settlement import OrderGapView, SettlementDetail, SettlementItemView, SettlementSummary
from app.services.spreadsheet import SpreadsheetError, read_tabular

MAX_SHEET_BYTES = 2_000_000
_HEADER = ("platform_order_id", "fee_type", "amount", "currency")
_LIST_LIMIT = 100


def _clean_key(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text[:128] or None


def parse_settlement_table(table: list[tuple[int, list[str]]]) -> list[SettlementLine]:
    if not table:
        raise SettlementFileInvalidError("结算文件是空的")
    header = tuple(cell.strip().lower() for cell in table[0][1])
    if header[:4] != _HEADER:
        raise SettlementFileInvalidError("表头必须是 platform_order_id,fee_type,amount,currency")
    lines: list[SettlementLine] = []
    for row_no, cells in table[1:]:
        padded = cells + [""] * (4 - len(cells))
        platform_order_id = padded[0].strip()
        fee_type = padded[1].strip()
        if not platform_order_id or len(platform_order_id) > 128:
            raise SettlementFileInvalidError(f"第 {row_no} 行订单号不合法")
        if not fee_type or len(fee_type) > 32:
            raise SettlementFileInvalidError(f"第 {row_no} 行费用类型不合法")
        try:
            amount = decimal_text(padded[2])
        except ValueError as exc:
            raise SettlementFileInvalidError(f"第 {row_no} 行金额不合法") from exc
        currency = padded[3].strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise SettlementFileInvalidError(f"第 {row_no} 行币种不合法")
        lines.append(
            SettlementLine(
                platform_order_id=platform_order_id,
                fee_type=fee_type,
                amount=amount,
                currency=currency,
            )
        )
    if not lines:
        raise SettlementFileInvalidError("结算文件没有明细")
    return lines


def _summary(row: Settlement) -> SettlementSummary:
    return SettlementSummary(
        id=row.id,
        shop_id=row.shop_id,
        platform_settlement_id=row.platform_settlement_id,
        period_start=row.period_start,
        period_end=row.period_end,
        amount=money_to_str(row.amount) or "0.000000",
        currency=row.currency,
        source=row.source,
        line_count=row.line_count,
        matched_count=row.matched_count,
        match_rate=money_to_str(row.match_rate) or "0.000000",
    )


def _item_view(row: SettlementItem) -> SettlementItemView:
    return SettlementItemView(
        id=row.id,
        platform_order_id=row.platform_order_id,
        order_id=row.order_id,
        fee_type=row.fee_type,
        amount=money_to_str(row.amount) or "0.000000",
        currency=row.currency,
        match_status=row.match_status,
    )


def _gap_view(gap: OrderGap) -> OrderGapView:
    return OrderGapView(
        platform_order_id=gap.platform_order_id,
        order_id=gap.order_id,
        order_amount=money_to_str(gap.order_amount),
        order_currency=gap.order_currency,
        settlement_amount=money_to_str(gap.settlement_amount),
        settlement_currency=gap.settlement_currency,
        deviation=money_to_str(gap.deviation),
        note=gap.note,
    )


class SettlementService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.rows = SettlementRepository(session)
        self.shops = ShopRepository(session)
        self.audit = AuditLogRepository(session)

    async def list_rows(self, *, limit: int) -> list[SettlementSummary]:
        found = await self.rows.list_visible(limit=min(limit, _LIST_LIMIT))
        return [_summary(row) for row in found]

    async def detail(self, settlement_id: int) -> SettlementDetail:
        row = await self.rows.get_or_404(settlement_id)
        return await self._detail(row)

    async def import_file(
        self,
        payload: bytes,
        filename: str,
        *,
        shop_id: int,
        platform_settlement_id: str,
        period_start: date,
        period_end: date,
        currency: str,
        tenant_id: int,
        actor_id: int,
        idempotency_key: str | None,
    ) -> SettlementDetail:
        if period_end < period_start:
            raise ParamInvalidError("结束日不能早于开始日")
        key = _clean_key(idempotency_key)
        if key:
            existing = await self.rows.get_by_key(key)
            if existing is not None:
                return await self._detail(existing)
        if len(payload) > MAX_SHEET_BYTES:
            raise SettlementFileInvalidError("结算文件过大")
        try:
            table = read_tabular(payload, filename or "upload.csv")
        except SpreadsheetError as exc:
            raise SettlementFileInvalidError("结算文件无法解析") from exc
        lines = parse_settlement_table(table)
        await self.shops.get_or_404(shop_id)
        settlement_no = platform_settlement_id.strip()
        if not settlement_no or len(settlement_no) > 128:
            raise ParamInvalidError("结算单号不合法")
        if await self.rows.get_natural(shop_id, settlement_no) is not None:
            raise AppError("同一店铺的结算单号已导入", code=ErrorCode.SETTLEMENT_MISMATCH)
        orders = await self._orders(shop_id, [line.platform_order_id for line in lines])
        matched, _gaps, rate = reconcile(lines, orders)
        amount = header_amount(lines, currency)
        hits = sum(1 for line in matched if line.status == MATCHED)
        row = Settlement(
            tenant_id=tenant_id,
            shop_id=shop_id,
            platform_settlement_id=settlement_no,
            period_start=period_start,
            period_end=period_end,
            amount=amount,
            currency=currency,
            source=filename.strip()[:500] or "upload.csv",
            line_count=len(matched),
            matched_count=hits,
            match_rate=quantize(rate),
            idempotency_key=key,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.rows.add(row)
        for line in matched:
            self.session.add(
                SettlementItem(
                    tenant_id=tenant_id,
                    settlement_id=row.id,
                    platform_order_id=line.platform_order_id,
                    order_id=line.order_id,
                    fee_type=line.fee_type,
                    amount=line.amount,
                    currency=line.currency,
                    match_status=line.status,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
        await self.session.flush()
        await self.audit.append_action(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=AuditAction.SETTLEMENT_IMPORT,
            resource="settlement",
            resource_id=row.id,
            before=None,
            after={
                "platform_settlement_id": settlement_no,
                "line_count": str(len(matched)),
                "matched_count": str(hits),
                "match_rate": f"{rate:.6f}",
            },
        )
        return await self._detail(row)

    async def _orders(self, shop_id: int, platform_order_ids: list[str]) -> dict[str, OrderRef]:
        found = await self.rows.orders_for(shop_id, list(dict.fromkeys(platform_order_ids)))
        return {row.platform_order_id: _order_ref(row) for row in found}

    async def _detail(self, row: Settlement) -> SettlementDetail:
        items = await self.rows.items_of(row.id)
        lines = [
            SettlementLine(
                platform_order_id=item.platform_order_id,
                fee_type=item.fee_type,
                amount=item.amount,
                currency=item.currency,
            )
            for item in items
        ]
        orders = await self._orders(row.shop_id, [item.platform_order_id for item in items])
        _matched, gaps, _rate = reconcile(lines, orders)
        summary = _summary(row)
        return SettlementDetail(
            id=summary.id,
            shop_id=summary.shop_id,
            platform_settlement_id=summary.platform_settlement_id,
            period_start=summary.period_start,
            period_end=summary.period_end,
            amount=summary.amount,
            currency=summary.currency,
            source=summary.source,
            line_count=summary.line_count,
            matched_count=summary.matched_count,
            match_rate=summary.match_rate,
            items=[_item_view(item) for item in items],
            gaps=[_gap_view(gap) for gap in gaps],
        )


def _order_ref(row: SalesOrder) -> OrderRef:
    return OrderRef(
        order_id=row.id,
        total_amount=row.total_amount,
        currency=str(row.currency).strip(),
    )
