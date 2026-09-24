"""pytest 全局夹具。

⚠️ 环境变量必须在**导入任何 app 模块之前**设置 ——
``app.core.config.settings`` 是模块级单例，导入即固化。
"""

from __future__ import annotations

import asyncio
import base64
import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("JWT_SECRET", "test-secret-please-do-not-use-in-production-0123456789")
# 固定构造 32 字节密钥（AES-256 要求），不用手工数长度
_TEST_AES_KEY = (b"crosspilot-test-secret" + b"0" * 32)[:32]
os.environ.setdefault("CREDENTIAL_AES_KEY", base64.urlsafe_b64encode(_TEST_AES_KEY).decode())

import pytest  # noqa: E402
from sqlalchemy import event  # noqa: E402

from app.core.config import get_settings  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _clear_settings_cache() -> None:
    """确保测试用 env 生效（settings 可能已被更早的导入固化）。"""
    get_settings.cache_clear()


def _raw_pg_dsn() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


async def _can_connect() -> bool:
    try:
        import asyncpg
    except ImportError:  # pragma: no cover
        return False
    try:
        conn = await asyncpg.connect(dsn=_raw_pg_dsn(), timeout=2)
    except Exception:
        return False
    await conn.close()
    return True


@pytest.fixture(scope="session")
def pg_available() -> bool:
    """本机是否有可用的 PostgreSQL。

    没有 Docker / 未起库时返回 False —— 依赖数据库的用例会被跳过而不是失败，
    这样"本机只跑纯逻辑测试"与"CI 跑全量"可以用同一套测试代码。
    """
    try:
        return asyncio.run(_can_connect())
    except RuntimeError:  # 已有事件循环（pytest-asyncio 场景）
        return False


@pytest.fixture(autouse=True)
def _reset_token_blacklist():
    """用例之间清空 jti 黑名单，避免登出/轮换状态串到下一个用例。"""
    from app.core.token_blacklist import reset_token_blacklist

    reset_token_blacklist()
    yield
    reset_token_blacklist()


@pytest.fixture(autouse=True)
def _reset_context_vars():
    """用例之间清空 contextvar，避免租户上下文串到下一个用例。"""
    from app.core import context as ctx

    ctx.set_trace_id(None)
    ctx.set_tenant_id(None)
    ctx.set_user_id(None)
    ctx.set_role_code(None)
    yield
    ctx.set_trace_id(None)
    ctx.set_tenant_id(None)
    ctx.set_user_id(None)
    ctx.set_role_code(None)


@pytest.fixture
def sqlite_engine():
    """内存 SQLite 引擎 —— 用于验证 ORM 层机制（不需要 PostgreSQL）。

    **函数级**作用域是刻意的：内存库在 session 级会跨用例累积数据，
    导致"租户 B 只看到自己那一行"这种断言被上一个用例的数据污染（很难排查）。

    只创建测试自己定义的租户表；生产模型里的 JSONB/ARRAY/INET 是 PG 专有类型，
    在 SQLite 上无法建表 —— 那些由集成测试在真实 PG 上验证。
    """
    from sqlalchemy import create_engine

    from app.db.base import Base
    from tests.unit.test_tenant_filter_mechanism import DemoTicket

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

    # SQLite 默认不启用外键约束，显式打开以便测试沿用生产行为
    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _record):  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine, tables=[DemoTicket.__table__])
    yield engine
    engine.dispose()
