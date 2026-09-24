"""Repository 基类。

**本层是唯一允许直接触达数据库的地方**（分层铁律，见 ``app/__init__.py``）。
好处：

1. 业务逻辑（services）可以纯单测，不需要起数据库；
2. 多租户条件集中在这里兜底 —— ORM 层已自动注入，这里是第二重保险；
3. 软删除过滤只写一次，不会出现"某个查询忘了带 deleted_at IS NULL"。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import get_tenant_id
from app.core.errors import CrossTenantError, NotFoundError
from app.db.base import Base, SoftDeleteMixin, TenantMixin

ModelT = TypeVar("ModelT", bound=Base)


def _has_soft_delete(model: type[Base]) -> bool:
    """运行时判断模型是否带软删除列。

    刻意包一层函数，而**不**直接写 ``issubclass(self.model, SoftDeleteMixin)``：
    ``SoftDeleteMixin`` 不继承 ``Base``，mypy 会把 ``self.model``（``type[ModelT]``，
    上界 Base）与 mixin 求交后收窄成空类型，从而把整个 if 分支判为「不可达」。
    """

    return issubclass(model, SoftDeleteMixin)


def _is_tenant_scoped(obj: object) -> bool:
    """运行时判断实例是否属于租户表（理由同上）。"""
    return isinstance(obj, TenantMixin)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------ 读
    def base_select(self) -> Select[tuple[ModelT]]:
        stmt = select(self.model)
        if _has_soft_delete(self.model):
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return stmt

    async def get(self, entity_id: int) -> ModelT | None:
        """按 ID 查询。

        跨租户访问会返回 ``None``（ORM 自动注入的 tenant 条件把它过滤掉了），
        调用方据此返回 404 —— **绝不要返回 403**，否则等于告诉攻击者
        "这个 ID 存在，只是不属于你"（PRD 13.2 第 4 条）。
        """
        stmt = self.base_select().where(self.model.id == entity_id)  # type: ignore[attr-defined]
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_or_404(self, entity_id: int) -> ModelT:
        obj = await self.get(entity_id)
        if obj is None:
            raise NotFoundError()
        return obj

    async def get_by(self, **filters: Any) -> ModelT | None:
        stmt = self.base_select()
        for key, value in filters.items():
            stmt = stmt.where(getattr(self.model, key) == value)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def exists(self, **filters: Any) -> bool:
        stmt = select(func.count()).select_from(self.model)
        if _has_soft_delete(self.model):
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        for key, value in filters.items():
            stmt = stmt.where(getattr(self.model, key) == value)
        return bool((await self.session.execute(stmt)).scalar_one())

    async def count(self) -> int:
        stmt = select(func.count()).select_from(self.model)
        if _has_soft_delete(self.model):
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return int((await self.session.execute(stmt)).scalar_one())

    # ------------------------------------------------------------ 写
    async def add(self, obj: ModelT) -> ModelT:
        """新增。tenant_id 由 ``tenant_filter._autofill_tenant_id`` 自动填充。"""
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def update(self, obj: ModelT, **values: Any) -> ModelT:
        self.assert_tenant_owned(obj)
        for key, value in values.items():
            setattr(obj, key, value)
        await self.session.flush()
        return obj

    async def soft_delete(self, obj: ModelT) -> None:
        if not _has_soft_delete(type(obj)):
            raise TypeError(f"{type(obj).__name__} 不支持软删除（流水表禁止删除）")
        self.assert_tenant_owned(obj)
        from datetime import UTC, datetime

        obj.deleted_at = datetime.now(UTC)  # type: ignore[attr-defined]
        await self.session.flush()

    # ------------------------------------------------------------ 防御
    def assert_tenant_owned(self, obj: ModelT) -> None:
        """写操作前的显式校验。

        ORM 的自动注入只覆盖「读」与「批量写」的 where 条件，
        对"拿到了对象再改字段"这种路径没有拦截能力 —— 所以写路径必须显式校验一次。
        """
        if not _is_tenant_scoped(obj):
            return
        current = get_tenant_id()
        if current is None:
            raise CrossTenantError("缺少租户上下文，拒绝写入")
        if int(obj.tenant_id) != int(current):  # type: ignore[attr-defined]
            raise CrossTenantError("跨租户写入被拒绝")
