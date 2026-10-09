"""批量发货与面单。每个订单一段独立保存点，失败单留下原因，可再次提交。"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import CredentialView
from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.errors import AdapterError
from app.adapters.registry import adapter_registry
from app.core.config import settings
from app.core.errors import AppError, ErrorCode, NotFoundError, ParamInvalidError
from app.core.logging import get_logger
from app.engines.order_desk import review_blocks_ship
from app.engines.order_privacy import mask_party
from app.engines.order_status import DecisionKind, StatusChangeSource, UnifiedStatus, decide_status, parse_unified
from app.models.enums import AuditAction
from app.models.order import SHIPMENT_FAILED, SHIPMENT_SUCCEEDED
from app.repositories.identity import AuditLogRepository
from app.repositories.order import OrderStatusLogRepository, SalesOrderRepository
from app.repositories.order_read import OrderReadRepository, ShipmentRepository
from app.repositories.platform import ShopCredentialRepository, ShopRepository
from app.schemas.order import BatchShipResult, FilePayload, LabelSkip, ShipItemResult
from app.services.credential_service import view_from_row
from app.services.label_pdf import build_label_pdf

log = get_logger(__name__)

TrackingFactory = Callable[[int, int], str]


@dataclass(frozen=True, slots=True)
class ShipTarget:
    order_id: int
    platform_order_id: str
    unified_status: str
    attempt: int
    shop_id: int = 0
    platform_code: str = ""
    tenant_id: int = 0
    platform_status: str = ""
    review_status: str = "AUTO_PASSED"


@dataclass(frozen=True, slots=True)
class ShipLine:
    order_id: int
    platform_order_id: str
    ok: bool
    tracking_no: str | None
    message: str


class ShipBook(Protocol):
    def atomic(self) -> AbstractAsyncContextManager[None]: ...

    async def load(self, order_id: int) -> ShipTarget | None: ...

    async def commit_success(
        self,
        target: ShipTarget,
        *,
        carrier: str,
        tracking_no: str,
        attempt: int,
    ) -> None: ...

    async def commit_failure(
        self,
        target: ShipTarget,
        *,
        carrier: str,
        reason: str,
        attempt: int,
    ) -> None: ...


class TrackingPusher(Protocol):
    async def push(self, target: ShipTarget, *, carrier: str, tracking_no: str) -> None: ...


def check_batch_size(count: int, limit: int) -> None:
    if count < 1:
        raise ParamInvalidError("请选择订单")
    if count > limit:
        raise ParamInvalidError(f"单批最多 {limit} 单")


def dedupe_ids(order_ids: list[int]) -> list[int]:
    seen: set[int] = set()
    unique: list[int] = []
    for order_id in order_ids:
        if order_id in seen:
            continue
        seen.add(order_id)
        unique.append(order_id)
    return unique


def refuse_ship(unified_status: str) -> str | None:
    decision = decide_status(
        parse_unified(unified_status),
        UnifiedStatus.SHIPPED,
        source=StatusChangeSource.MANUAL,
    )
    if decision.kind is DecisionKind.APPLY:
        return None
    return "当前状态不能发货"


def make_tracking_no(order_id: int, attempt: int) -> str:
    return f"{settings.order_tracking_prefix}{order_id}A{attempt}"[:64]


async def run_ship_batch(
    book: ShipBook,
    pusher: TrackingPusher,
    *,
    order_ids: list[int],
    carrier: str,
    tracking_for: TrackingFactory = make_tracking_no,
) -> list[ShipLine]:
    lines: list[ShipLine] = []
    for order_id in order_ids:
        lines.append(await _ship_one(book, pusher, order_id=order_id, carrier=carrier, tracking_for=tracking_for))
    return lines


async def _ship_one(
    book: ShipBook,
    pusher: TrackingPusher,
    *,
    order_id: int,
    carrier: str,
    tracking_for: TrackingFactory,
) -> ShipLine:
    target = await book.load(order_id)
    if target is None:
        return ShipLine(order_id, "", False, None, "订单不存在")
    reason = refuse_ship(target.unified_status) or review_blocks_ship(target.review_status)
    if reason is not None:
        return ShipLine(target.order_id, target.platform_order_id, False, None, reason)
    attempt = target.attempt + 1
    tracking_no = tracking_for(target.order_id, attempt)
    try:
        async with book.atomic():
            await pusher.push(target, carrier=carrier, tracking_no=tracking_no)
            await book.commit_success(target, carrier=carrier, tracking_no=tracking_no, attempt=attempt)
    except Exception as exc:
        message = _public_reason(exc)
        await _record_failure(book, target, carrier=carrier, reason=message, attempt=attempt)
        return ShipLine(target.order_id, target.platform_order_id, False, None, message)
    return ShipLine(target.order_id, target.platform_order_id, True, tracking_no, "已回传平台")


async def _record_failure(
    book: ShipBook,
    target: ShipTarget,
    *,
    carrier: str,
    reason: str,
    attempt: int,
) -> None:
    try:
        async with book.atomic():
            await book.commit_failure(target, carrier=carrier, reason=reason, attempt=attempt)
    except Exception:
        log.exception("ship_failure_not_recorded", order_id=target.order_id)


def _public_reason(exc: Exception) -> str:
    if isinstance(exc, (AdapterError, AppError)):
        text = str(exc).strip()
        return (text or "发货回传失败")[:200]
    return "发货回传失败"


class DbShipBook:
    def __init__(self, session: AsyncSession, *, operator_id: int) -> None:
        self.session = session
        self.operator_id = operator_id
        self.orders = SalesOrderRepository(session)
        self.shipments = ShipmentRepository(session)
        self.logs = OrderStatusLogRepository(session)

    @asynccontextmanager
    async def atomic(self) -> AsyncIterator[None]:
        async with self.session.begin_nested():
            yield

    async def load(self, order_id: int) -> ShipTarget | None:
        order = await self.orders.get(order_id)
        if order is None:
            return None
        shipment = await self.shipments.get_for_order(order.id)
        return ShipTarget(
            order_id=order.id,
            platform_order_id=order.platform_order_id,
            unified_status=order.unified_status,
            attempt=0 if shipment is None else shipment.attempt,
            shop_id=order.shop_id,
            platform_code=order.platform_code,
            tenant_id=order.tenant_id,
            platform_status=order.platform_status,
            review_status=order.review_status,
        )

    async def commit_success(
        self,
        target: ShipTarget,
        *,
        carrier: str,
        tracking_no: str,
        attempt: int,
    ) -> None:
        order = await self.orders.get(target.order_id)
        if order is None:
            raise NotFoundError("订单不存在", code=ErrorCode.ORDER_NOT_FOUND)
        now = datetime.now(UTC)
        previous = order.unified_status
        order.unified_status = UnifiedStatus.SHIPPED.value
        order.shipped_at = now
        order.updated_by = self.operator_id
        await self.logs.append(
            tenant_id=order.tenant_id,
            order_id=order.id,
            from_status=previous,
            to_status=UnifiedStatus.SHIPPED.value,
            platform_status=order.platform_status,
            operator_id=self.operator_id,
            source=StatusChangeSource.MANUAL.value,
            remark=f"{carrier} {tracking_no}"[:500],
        )
        await self.shipments.save(
            tenant_id=order.tenant_id,
            order_id=order.id,
            carrier=carrier,
            tracking_no=tracking_no,
            status=SHIPMENT_SUCCEEDED,
            failure_reason=None,
            shipped_at=now,
            attempt=attempt,
            operator_id=self.operator_id,
        )
        from app.services.inventory import InventoryService

        await InventoryService(self.session).ship_order(order.id)

    async def commit_failure(
        self,
        target: ShipTarget,
        *,
        carrier: str,
        reason: str,
        attempt: int,
    ) -> None:
        await self.shipments.save(
            tenant_id=target.tenant_id,
            order_id=target.order_id,
            carrier=carrier,
            tracking_no="",
            status=SHIPMENT_FAILED,
            failure_reason=reason,
            shipped_at=None,
            attempt=attempt,
            operator_id=self.operator_id,
        )


class AdapterTrackingPusher:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shops = ShopRepository(session)
        self.credentials = ShopCredentialRepository(session)
        register_builtin_adapters()

    async def push(self, target: ShipTarget, *, carrier: str, tracking_no: str) -> None:
        shop = await self.shops.get(target.shop_id)
        if shop is None:
            raise AdapterError("店铺不存在", platform=target.platform_code)
        credential = await self.credentials.get_by_shop_id(shop.id)
        if credential is None:
            raise AdapterError("店铺凭证不存在", platform=target.platform_code)
        view: CredentialView = view_from_row(shop, credential)
        await adapter_registry.get(target.platform_code).ship_order(
            view,
            target.platform_order_id,
            carrier,
            tracking_no,
        )


class OrderShipService:
    def __init__(self, session: AsyncSession, *, pusher: TrackingPusher | None = None) -> None:
        self.session = session
        self.reader = OrderReadRepository(session)
        self.audit = AuditLogRepository(session)
        self.pusher = pusher or AdapterTrackingPusher(session)

    async def batch_ship(
        self,
        *,
        order_ids: list[int],
        carrier: str,
        operator_id: int,
        tenant_id: int,
    ) -> BatchShipResult:
        unique = dedupe_ids(order_ids)
        check_batch_size(len(unique), settings.order_batch_ship_limit)
        carrier_text = carrier.strip()
        if not carrier_text:
            raise ParamInvalidError("请填写承运商")
        lines = await run_ship_batch(
            DbShipBook(self.session, operator_id=operator_id),
            self.pusher,
            order_ids=unique,
            carrier=carrier_text,
        )
        failed = sum(1 for line in lines if not line.ok)
        await self.audit.append_action(
            tenant_id=tenant_id,
            action=AuditAction.BATCH_SHIP,
            resource="order",
            user_id=operator_id,
            after={
                "carrier": carrier_text,
                "succeeded": len(lines) - failed,
                "failed": failed,
                "order_ids": [str(line.order_id) for line in lines],
            },
        )
        return BatchShipResult(
            succeeded=len(lines) - failed,
            failed=failed,
            results=[_result(line) for line in lines],
        )

    async def print_labels(self, *, order_ids: list[int], size: str, role_code: str) -> FilePayload:
        unique = dedupe_ids(order_ids)
        check_batch_size(len(unique), settings.order_batch_ship_limit)
        pages: list[list[str]] = []
        skipped: list[LabelSkip] = []
        for order_id in unique:
            hit = await self.reader.get_order(order_id)
            if hit is None:
                skipped.append(LabelSkip(order_id=order_id, message="订单不存在"))
                continue
            if hit.shipment_status != SHIPMENT_SUCCEEDED or not hit.tracking_no:
                skipped.append(
                    LabelSkip(
                        order_id=hit.order.id,
                        platform_order_id=hit.order.platform_order_id,
                        message="还没有成功的运单",
                    )
                )
                continue
            _buyer, ship = mask_party(role_code, hit.order.buyer_info, hit.order.ship_to)
            pages.append(_label_lines(hit.order.platform_order_id, hit.carrier or "", hit.tracking_no, ship))
        if not pages:
            raise ParamInvalidError("没有可打印的面单")
        pdf = build_label_pdf(pages, size=size)
        return FilePayload(
            filename=f"labels-{size}.pdf",
            content_type="application/pdf",
            content_base64=base64.b64encode(pdf).decode("ascii"),
            row_count=len(pages),
            skipped=skipped,
        )


def _result(line: ShipLine) -> ShipItemResult:
    return ShipItemResult(
        order_id=line.order_id,
        platform_order_id=line.platform_order_id,
        ok=line.ok,
        tracking_no=line.tracking_no,
        message=line.message,
    )


def _label_lines(
    platform_order_id: str,
    carrier: str,
    tracking_no: str,
    ship: dict[str, str | None],
) -> list[str]:
    lines = [
        f"Order {platform_order_id}",
        f"{carrier} {tracking_no}".strip(),
    ]
    for key in ("name", "phone", "line1", "city", "state", "postal_code", "country"):
        value = ship.get(key)
        if value:
            lines.append(value)
    return lines


__all__ = [
    "AdapterTrackingPusher",
    "DbShipBook",
    "OrderShipService",
    "ShipBook",
    "ShipLine",
    "ShipTarget",
    "TrackingPusher",
    "check_batch_size",
    "dedupe_ids",
    "make_tracking_no",
    "refuse_ship",
    "run_ship_batch",
]
