"""★ 多租户隔离第一道防线：ORM 层自动注入租户条件。

## 为什么必须在 ORM 层做

人工在每个查询里写 ``where(tenant_id == ...)`` 一定会漏。漏一次就是**数据串租户**——
PRD 13.3 把它列为灾难级事故。所以在这里做成"默认安全"：

| 场景 | 行为 |
|---|---|
| SELECT 租户表 | 自动追加 ``tenant_id = <当前上下文>``；无租户上下文 → **直接报错**（fail-closed） |
| 关联/延迟加载 | 同样自动追加（``include_aliases=True``） |
| UPDATE / DELETE 租户表 | 若 WHERE 里没有 tenant_id → **拒绝执行**（防"一条语句改全租户"） |
| INSERT 租户表 | ``before_flush`` 自动填充当前 tenant_id |
| 非租户表（tenant / sys_user / platform 字典） | 完全不干预 |

## 三道防线全景（约束 C1）

1. **ORM 自动注入**（本模块）—— 覆盖 99% 的日常查询；
2. **PostgreSQL RLS**（``alembic/versions/0001_*.py``）—— 即使有人绕过 ORM 裸写 SQL 也挡住；
3. **CI 越权测试**（``tests/security/test_tenant_isolation.py``）—— 新增租户表必须同步补用例。

## 逃生舱

仅限系统级任务（如 Celery 定时扫描全平台店铺）使用，且会在日志里留痕::

    stmt = select(Shop).execution_options(**{SKIP_FLAG: True})
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Column, event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria
from sqlalchemy.sql import visitors
from sqlalchemy.sql.dml import Delete, Update

from app.core.context import get_tenant_id
from app.core.errors import UnauthenticatedError
from app.core.logging import get_logger
from app.db.base import TenantMixin

log = get_logger(__name__)

TENANT_COLUMN = "tenant_id"
SKIP_FLAG = "skip_tenant_filter"


class UnscopedTenantWriteError(RuntimeError):
    """对租户表执行了无租户条件的批量写。

    这是**代码缺陷**而非业务错误，所以直接抛 RuntimeError（→ 500），
    宁可让请求失败，也不能让它静默地把所有租户的数据一起改掉。
    """


def _current_tenant() -> int | None:
    return get_tenant_id()


def _tenant_criteria() -> Any:
    """构造租户过滤条件。

    ⚠️ ``tenant_id`` 必须是**闭包局部变量**：lambda SQL 系统要能在不执行 lambda
    的前提下提取绑定值，所以它只认闭包变量，不认函数调用 ——
    ``lambda cls: cls.tenant_id == get_tenant_id()`` 会被 SQLAlchemy 直接拒绝。

    每次执行都新建一个 lambda，闭包里装的是**当次请求**的租户 ID。
    绝不能预先建好条件对象复用 —— 那会把第一个请求的租户 ID 固化，
    导致后续所有请求都查同一个租户，属于灾难级事故。
    """
    tenant_id = _current_tenant()
    return with_loader_criteria(
        TenantMixin,
        lambda cls: cls.tenant_id == tenant_id,
        include_aliases=True,
    )


def _has_tenant_predicate(clause: Any) -> bool:
    """判断 WHERE 子句里是否出现了 tenant_id 列。"""
    if clause is None:
        return False
    return any(isinstance(el, Column) and el.name == TENANT_COLUMN for el in visitors.iterate(clause))


def _guard_dml(state: ORMExecuteState) -> None:
    """批量 UPDATE / DELETE 的 fail-closed 检查。

    注意 ``with_loader_criteria`` 对 DML **不生效**（它只作用于 SELECT），
    所以批量写必须在这里硬拦：WHERE 里没有 tenant_id 就直接拒绝。
    """
    statement = state.statement
    if not isinstance(statement, (Update, Delete)):
        return
    target = statement.table
    if TENANT_COLUMN not in target.c:
        return  # 非租户表，不干预
    if _has_tenant_predicate(statement.whereclause):
        return
    # 目标可能是 TableClause / Alias / Join —— 只有前两者有 name，取不到就退回字符串
    label = getattr(target, "name", None) or str(target)
    raise UnscopedTenantWriteError(
        f"拒绝对租户表执行无租户条件的批量写：{label}。请在 WHERE 中显式带上 tenant_id，或改用 repository 层方法。"
    )


@event.listens_for(Session, "do_orm_execute")
def _enforce_tenant_isolation(state: ORMExecuteState) -> None:
    """ORM 执行钩子 —— 所有 ORM 语句的必经之路。"""
    # ---------------- 逃生舱（系统级任务，留痕） ----------------
    if state.execution_options.get(SKIP_FLAG):
        log.warning("tenant_filter_skipped", statement=str(state.statement)[:200])
        return

    # ---------------- 批量写：fail-closed ----------------
    if state.is_update or state.is_delete:
        _guard_dml(state)
        return

    # 只处理读路径（含列加载与关系加载）
    if not (state.is_select or state.is_column_load or state.is_relationship_load):
        return

    tenant_id = _current_tenant()

    if tenant_id is None:
        # 无租户上下文却要查租户数据 —— 这是漏洞而不是巧合，直接拒绝
        tenant_mappers = [m for m in (state.all_mappers or ()) if issubclass(m.class_, TenantMixin)]
        if tenant_mappers:
            names = ", ".join(sorted(m.class_.__name__ for m in tenant_mappers))
            raise UnauthenticatedError(f"缺少租户上下文，拒绝查询租户数据：{names}")
        return

    state.statement = state.statement.options(_tenant_criteria())


@event.listens_for(Session, "before_flush")
def _autofill_tenant_id(session: Session, _flush_context: Any, _instances: Any) -> None:
    """新增租户表记录时自动填 tenant_id，避免"忘了赋值"直接 500。"""
    tenant_id = get_tenant_id()
    if tenant_id is None:
        return
    for obj in session.new:
        if isinstance(obj, TenantMixin) and getattr(obj, "tenant_id", None) is None:
            obj.tenant_id = tenant_id


@contextmanager
def tenant_scope_bypass() -> Iterator[None]:
    """临时关闭租户过滤的调试开关 —— **仅限测试**，不要在生产代码里用。"""
    tid = get_tenant_id()
    from app.core.context import _tenant_id

    token = _tenant_id.set(None)
    try:
        yield
    finally:
        _tenant_id.reset(token)
        if tid is not None:
            _tenant_id.set(tid)


__all__ = [
    "SKIP_FLAG",
    "TENANT_COLUMN",
    "UnscopedTenantWriteError",
    "tenant_scope_bypass",
]
