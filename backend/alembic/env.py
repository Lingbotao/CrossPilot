"""Alembic 环境。

要点：
1. 连接串从 ``settings.database_migration_url`` 注入 —— **不在版本库里写密钥**；
   并且刻意用**迁移账号**（表 owner）：运行时账号受 RLS 约束，
   如果用它跑迁移，遇到 RLS 策略会导致迁移失败或数据写不进去。
2. ``compare_type=True`` + ``compare_server_default=True`` —— 默认不开这两项，
   改了字段类型或默认值却生成空迁移，是很典型的"改了没生效"事故来源。
3. 支持离线模式（``alembic upgrade head --sql``）—— 无需数据库即可产出 SQL，
   便于在无 Docker 环境下审阅 DDL（本地开发常用）。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import settings
from app.db.base import Base

# ⚠️ 必须 import 模型包，否则 autogenerate 看不到表（会生成"删表"迁移！）
import app.models  # noqa: F401  isort:skip

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# URL 里若含 % 会被 configparser 当成插值语法，必须转义
_migration_url = settings.database_migration_url.replace("%", "%%")
config.set_main_option("sqlalchemy.url", _migration_url)


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库（用于审阅与交付 DDL）。"""
    context.configure(
        url=settings.database_migration_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
        # 分区表由 postgresql_partition_by 处理，不需要额外特殊逻辑
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
