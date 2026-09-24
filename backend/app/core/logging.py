"""结构化日志（PRD NFR-O-01）。

要求：JSON 格式，且**每条日志都带 trace_id / tenant_id / user_id**。
实现方式：trace_id 等存放在 contextvar（见 ``app.core.context``），
通过一个 processor 自动合并 —— 业务代码只管 ``log.info("xxx")``，
不需要手动传上下文字段（手动传必然会漏）。

## 为什么绕了一道 stdlib

uvicorn / sqlalchemy / celery 都用标准库 ``logging``。如果 structlog 自己一套、
标准库另一套，日志就会出现两种格式混排，JSON 日志解析器直接报错。

所以：**structlog 不自己渲染**，而是通过 ``ProcessorFormatter.wrap_for_formatter``
把事件交给标准库的 Formatter 统一渲染。这样业务日志与框架日志走同一条管道。
（如果 structlog 自己渲染、ProcessorFormatter 又渲染一次，就会出现
 ``"event": "{\\"event\\": ...}"`` 这种双重转义的嵌套字符串 —— 调试起来很费时间。）
"""

from __future__ import annotations

import logging
import sys
from typing import Any, cast

import structlog
from structlog.types import EventDict, Processor

from app.core.config import settings
from app.core.context import bind_context_values

_configured = False


def _merge_request_context(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """把 contextvar 里的请求上下文并进日志。已有同名字段时不覆盖（显式传参优先）。"""
    for key, value in bind_context_values().items():
        event_dict.setdefault(key, value)
    return event_dict


def _drop_noise(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    """剔除 uvicorn 注入的带 ANSI 颜色的 message（JSON 里会是乱码）。"""
    event_dict.pop("color_message", None)
    return event_dict


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        _merge_request_context,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _drop_noise,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]


def _renderer() -> Processor:
    if settings.log_format == "json":
        return structlog.processors.JSONRenderer(ensure_ascii=False, sort_keys=False)
    return structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())


def configure_logging(force: bool = False) -> None:
    """进程启动时调用一次（``app.main`` 与 Celery 都调用）。"""
    global _configured
    if _configured and not force:
        return

    level = getattr(logging, settings.log_level, logging.INFO)

    structlog.configure(
        processors=[*_shared_processors(), structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # foreign_pre_chain：标准库 logger 调用时，先把上下文补齐再渲染
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _renderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # 框架日志降噪：不改级别的话 uvicorn.access 会把每个请求刷两遍
    for noisy, noisy_level in {
        "uvicorn": logging.INFO,
        "uvicorn.error": logging.INFO,
        "uvicorn.access": logging.WARNING,
        "sqlalchemy.engine": logging.INFO if settings.db_echo else logging.WARNING,
        "asyncio": logging.WARNING,
        "botocore": logging.WARNING,
        "celery": logging.INFO,
    }.items():
        std_logger = logging.getLogger(noisy)
        std_logger.setLevel(noisy_level)
        std_logger.propagate = True

    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """业务代码统一入口：``log = get_logger(__name__)``。"""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
