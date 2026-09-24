"""请求级上下文（contextvar）。

为什么要用 contextvar 而不是全局变量：
    FastAPI 在同一个事件循环里并发处理请求，全局变量会串号 —— 串号在多租户系统里
    等于**数据串租户**，是灾难级事故。contextvar 天然按 async 任务隔离。

另一个关键点（PRD NFR-O-01）：Celery 任务**不会**自动继承 contextvar。
跨进程/跨线程时必须显式传 ``tenant_id``，见 ``app/tasks/celery_app.py`` 的约定。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from app.core.errors import UnauthenticatedError

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_tenant_id: ContextVar[int | None] = ContextVar("tenant_id", default=None)
_user_id: ContextVar[int | None] = ContextVar("user_id", default=None)
_role_code: ContextVar[str | None] = ContextVar("role_code", default=None)


# ------------------------------------------------------------------ 读取
def get_trace_id() -> str | None:
    return _trace_id.get()


def get_tenant_id() -> int | None:
    return _tenant_id.get()


def get_user_id() -> int | None:
    return _user_id.get()


def get_role_code() -> str | None:
    return _role_code.get()


def require_tenant_id() -> int:
    """拿不到租户上下文即拒绝——宁可报错，也不能让查询退化成「全租户」。"""
    tid = _tenant_id.get()
    if tid is None:
        raise UnauthenticatedError("缺少租户上下文")
    return tid


def require_user_id() -> int:
    uid = _user_id.get()
    if uid is None:
        raise UnauthenticatedError("缺少用户上下文")
    return uid


# ------------------------------------------------------------------ 写入
def set_trace_id(value: str | None) -> Token[str | None]:
    return _trace_id.set(value)


def set_tenant_id(value: int | None) -> Token[int | None]:
    return _tenant_id.set(value)


def set_user_id(value: int | None) -> Token[int | None]:
    return _user_id.set(value)


def set_role_code(value: str | None) -> Token[str | None]:
    return _role_code.set(value)


@contextmanager
def tenant_context(tenant_id: int, user_id: int | None = None, role_code: str | None = None) -> Iterator[None]:
    """供 Celery 任务 / 脚本显式建立租户上下文使用。

    ``with tenant_context(tid): ...`` —— 退出时自动还原，避免污染后续任务。
    """
    tokens: list[tuple[ContextVar[Any], Token[Any]]] = [
        (_tenant_id, _tenant_id.set(tenant_id)),
    ]
    if user_id is not None:
        tokens.append((_user_id, _user_id.set(user_id)))
    if role_code is not None:
        tokens.append((_role_code, _role_code.set(role_code)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def bind_context_values() -> dict[str, Any]:
    """给 structlog 用的扁平字典（None 值不落日志，避免噪声）。"""
    pairs: dict[str, Any] = {
        "trace_id": _trace_id.get(),
        "tenant_id": _tenant_id.get(),
        "user_id": _user_id.get(),
        "role_code": _role_code.get(),
    }
    return {k: v for k, v in pairs.items() if v is not None}
