"""异步数据库会话。

三件事在这里一次性做掉：
1. **连接池统一配置**（含 statement_timeout —— 防慢查询把连接池拖干）；
2. **RLS 会话变量注入**：``select set_config('app.current_tenant', :tid, true)``
   —— 这是多租户**第二道防线**（PostgreSQL 行级安全）的开关；
3. **事务边界**：一个请求一个事务，成功提交、异常回滚。

Celery 任务不会自动继承请求上下文，必须用 ``app.core.context.tenant_context``
显式建立租户上下文后再开 session。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.context import get_tenant_id
from app.core.logging import get_logger

log = get_logger(__name__)

_engine: AsyncEngine | None = None


def _build_connect_args(url: str) -> dict[str, object]:
    """asyncpg 专属参数：只对 PG 生效，换驱动时不至于报未知参数。"""
    if not url.startswith("postgresql"):
        return {}
    return {
        "server_settings": {
            "statement_timeout": str(settings.db_statement_timeout_ms),
            "timezone": "UTC",  # 全系统统一 UTC 存取
            "application_name": "crosspilot-api",
        }
    }


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_pre_ping=True,  # 防「连接被中间件静默掐断」导致的随机 500
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_recycle=1800,
            connect_args=_build_connect_args(settings.database_url),
        )
        log.info("db_engine_created", pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False, autoflush=False)


async def dispose_engine() -> None:
    global _engine, _owner_engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
    if _owner_engine is not None:
        await _owner_engine.dispose()
        _owner_engine = None


async def apply_rls_tenant(session: AsyncSession, tenant_id: int | None) -> None:
    """把租户 ID 写入 PG 会话变量，激活 RLS 策略。

    ``set_config(..., is_local=True)`` 的作用域是**当前事务**，
    事务结束自动失效 —— 连接回到池里不会被下一个请求继承，这是必须用
    is_local=True 的原因（否则会出现「串租户」的隐蔽 bug）。
    """
    if tenant_id is None:
        return
    await session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：请求级会话（**不含 RLS 绑定**）。

    ⚠️ 这里刻意不绑定 RLS。原因：RLS 变量必须在**租户上下文确定之后**才能写，
    而 ``get_db`` 通常先于鉴权依赖执行。如果在这里"顺手绑一下"，
    会给人"已经安全了"的错觉，实际绑的是空上下文。

    正确做法见 ``app.core.deps.get_identity``：先解析令牌 → 写上下文 → 再绑 RLS。

    忘记绑定的后果是**默认拒绝**（策略里读不到 ``app.current_tenant`` 时
    ``tenant_id = NULL`` 恒为 false，一行都查不出来），而不是放开全部数据 ——
    这是刻意设计的 fail-closed 姿态。
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope(tenant_id: int | None = None) -> AsyncIterator[AsyncSession]:
    """脚本 / Celery 任务用的会话上下文。

    用法::

        async with session_scope(tenant_id=shop.tenant_id) as session:
            ...
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            await apply_rls_tenant(session, tenant_id if tenant_id is not None else get_tenant_id())
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


_owner_engine: AsyncEngine | None = None


def get_owner_engine() -> AsyncEngine:
    """迁移角色（表 owner）引擎 —— **仅供运维路径**。

    用 ``database_migration_url`` 连接。表 owner 默认绕过 RLS，
    这正是迁移 / 种子 / 数据修复能够跨租户工作的前提。

    ⚠️ 运行时请求路径绝不能碰它：一旦用了，多租户第二道防线等于关闭。
    """
    global _owner_engine
    if _owner_engine is None:
        _owner_engine = create_async_engine(
            settings.database_migration_url,
            echo=settings.db_echo,
            pool_pre_ping=True,
            pool_size=2,  # 运维路径并发极低，池子开小
            max_overflow=2,
            pool_recycle=1800,
            connect_args=_build_connect_args(settings.database_migration_url),
        )
        log.info("db_owner_engine_created", purpose="migration/seed only")
    return _owner_engine


@asynccontextmanager
async def owner_session_scope() -> AsyncIterator[AsyncSession]:
    """初始化 / 运维专用会话：以**表 owner** 身份连接，绕过 RLS。

    为什么种子脚本必须用它：
    ``tenant`` 表的 INSERT 策略允许"注册期（尚无租户上下文）插入"，
    但 ORM 的 ``flush()`` 会带上 ``INSERT ... RETURNING``，而 PostgreSQL 对
    ``INSERT ... RETURNING`` **还要再过一遍 SELECT 策略** —— 此时
    ``app.current_tenant`` 尚未设置，SELECT 策略恒为 false，插入被拒。

    这里不去放宽 SELECT 策略（那会让"忘记绑定租户"变成"能读到全部租户数据"，
    彻底毁掉 fail-closed 姿态），而是让初始化路径显式走 owner。

    ⚠️ 业务代码不要用。请求路径请走 ``app.core.deps.get_identity``。
    """
    factory = async_sessionmaker(get_owner_engine(), class_=AsyncSession, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_db_health() -> tuple[bool, int | None]:
    """健康检查：返回 (是否可用, 往返毫秒)。失败不抛异常 —— 健康检查自己不能把服务打挂。"""
    import time

    started = time.perf_counter()
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        log.warning("db_health_failed", error=str(exc))
        return False, None
    return True, int((time.perf_counter() - started) * 1000)
