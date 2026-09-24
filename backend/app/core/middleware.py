"""中间件：trace_id 注入 + 请求日志。"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from ulid import ULID

from app.core.context import set_trace_id
from app.core.logging import get_logger

log = get_logger(__name__)

TRACE_HEADER = "X-Trace-Id"

# 健康检查与静态资源不打日志，否则日志会被探针刷爆
_SILENT_PATHS = frozenset({"/healthz", "/readyz", "/metrics", "/favicon.ico"})


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为每个请求生成 trace_id（ULID）。

    为什么用 ULID 而不是 UUID4：ULID 前 48 位是毫秒时间戳 —— 支持按时间范围
    扫描日志，且字典序 = 时间序。UUID4 随机分布，无法按时间切分。

    客户端可传 ``X-Trace-Id`` 透传（便于网关/前端串联），但会做长度与字符校验，
    防止有人塞 10KB 垃圾进日志（日志注入）。
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(TRACE_HEADER, "")
        trace_id = incoming if _is_valid_trace_id(incoming) else str(ULID())
        set_trace_id(trace_id)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            # 异常由 exception_handler 记录详细信息，这里只标记请求结束
            log.warning(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[TRACE_HEADER] = trace_id

        if request.url.path not in _SILENT_PATHS:
            log.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        return response


def _is_valid_trace_id(value: str) -> bool:
    return 8 <= len(value) <= 64 and all(c.isalnum() or c in "-_" for c in value)
