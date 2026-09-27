"""订单状态：映射表解析、九态判定、变更日志。

同步和 Webhook 都走 ``ingest_order``。库里的映射覆盖适配器默认值，
所以新增一种平台原文时只改表，不改业务分支。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import UnifiedOrder
from app.adapters.bootstrap import register_builtin_adapters
from app.adapters.registry import SUPPORTED_PLATFORMS, adapter_registry
from app.core.errors import ErrorCode, NotFoundError, ParamInvalidError, PlatformUnsupportedError
from app.engines.order_status import (
    UNMAPPED_REMARK,
    DecisionKind,
    StatusChangeSource,
    StatusDecision,
    decide_status,
    merge_mapping,
    parse_unified,
    resolve_target,
)
from app.models.config import PlatformStatusMapping
from app.models.order import OrderStatusLog
from app.models.platform import Shop
from app.repositories.order import OrderStatusLogRepository, SalesOrderRepository
from app.repositories.status_mapping import PlatformStatusMappingRepository
from app.services.order_desk import OrderDeskService
from app.sync_engine.idempotency import WriteAction, order_idempotency_key


class OrderStatusService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = SalesOrderRepository(session)
        self.logs = OrderStatusLogRepository(session)
        self.mappings = PlatformStatusMappingRepository(session)
        self._tables: dict[str, dict[str, str]] = {}
        register_builtin_adapters()

    async def list_mappings(self, platform: str | None) -> list[PlatformStatusMapping]:
        code = platform.strip().lower() if platform else None
        if code is not None and code not in SUPPORTED_PLATFORMS:
            raise PlatformUnsupportedError()
        targets = (code,) if code is not None else SUPPORTED_PLATFORMS
        for item in targets:
            await self.mapping_table(item)
        return await self.mappings.list_rows(code)

    async def save_mapping(
        self,
        *,
        platform_code: str,
        platform_status: str,
        unified_status: str,
        user_id: int,
    ) -> PlatformStatusMapping:
        if platform_code not in SUPPORTED_PLATFORMS:
            raise PlatformUnsupportedError()
        if parse_unified(unified_status) is None:
            raise ParamInvalidError("统一状态不在九态之内")
        row = await self.mappings.save(
            platform_code=platform_code,
            platform_status=platform_status,
            unified_status=unified_status,
            user_id=user_id,
        )
        self._tables.pop(platform_code, None)
        return row

    async def list_logs(self, order_id: int) -> list[OrderStatusLog]:
        order = await self.orders.get(order_id)
        if order is None:
            raise NotFoundError("订单不存在", code=ErrorCode.ORDER_NOT_FOUND)
        return await self.logs.list_for_order(order.id)

    async def mapping_table(self, platform: str) -> dict[str, str]:
        cached = self._tables.get(platform)
        if cached is not None:
            return cached
        defaults = {
            key: value
            for key, value in adapter_registry.get(platform).status_mapping().items()
            if parse_unified(value) is not None
        }
        await self.mappings.insert_missing(platform, defaults)
        stored = await self.mappings.as_dict(platform)
        merged = merge_mapping(defaults, stored)
        self._tables[platform] = merged
        return merged

    async def ingest_order(
        self,
        shop: Shop,
        order: UnifiedOrder,
        *,
        source: StatusChangeSource = StatusChangeSource.SYSTEM,
        operator_id: int | None = None,
    ) -> tuple[WriteAction, StatusDecision | None]:
        table = await self.mapping_table(shop.platform_code)
        target = resolve_target(order.platform_status, table)
        key = order_idempotency_key(shop.platform_code, shop.id, order.platform_order_id)

        def resolve(current: str | None) -> StatusDecision:
            return decide_status(parse_unified(current), target, source=source)

        action, decision = await self.orders.upsert(shop, order, resolve=resolve)
        if action is WriteAction.SKIP:
            return action, decision
        row = await self.orders.get_by_key(key)
        if row is None:
            return action, decision
        if decision is not None and decision.kind == DecisionKind.APPLY:
            await self.logs.append(
                tenant_id=shop.tenant_id,
                order_id=row.id,
                from_status=None if decision.from_status is None else decision.from_status.value,
                to_status=decision.to_status.value,
                platform_status=order.platform_status,
                operator_id=operator_id,
                source=source.value,
                remark=UNMAPPED_REMARK if decision.unmapped else None,
            )
        await OrderDeskService(self.session).apply_review(row)
        return action, decision


__all__ = ["OrderStatusService"]
