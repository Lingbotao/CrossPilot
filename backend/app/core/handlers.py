"""全局异常处理。

**目标：前端永远拿到同一个信封，永远不用解析堆栈。**

| 异常类型 | 对外 code | 对外 message |
|---|---|---|
| ``AppError``（业务异常） | 业务码 | 业务话术（可直出） |
| ``RequestValidationError`` | 10001 | 字段级错误详情 |
| ``HTTPException`` | 按状态码映射 | 简短描述 |
| 其它未预期异常 | 10099 | **"服务内部错误"**（堆栈只进日志） |

最后一行是安全底线：未预期异常的堆栈里常有表名、SQL、内部路径，
直接返回给前端等于免费送情报。
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.core.response import fail

log = get_logger(__name__)

_HTTP_STATUS_TO_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.PARAM_INVALID,
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.PERMISSION_DENIED,
    404: ErrorCode.RESOURCE_NOT_FOUND,
    405: ErrorCode.PARAM_INVALID,
    409: ErrorCode.OPERATION_CONFLICT,
    422: ErrorCode.PARAM_INVALID,
    429: ErrorCode.TOO_MANY_REQUESTS,
}


def _json(payload: Any, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        body = fail(int(exc.code), exc.message, exc.data or None).model_dump(mode="json")
        if exc.status >= 500:
            log.error("app_error_5xx", code=int(exc.code), message=exc.message)
        else:
            log.info("app_error", code=int(exc.code), message=exc.message)
        return _json(body, exc.status)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # 把 pydantic 的错误结构压成「字段 → 原因」，前端可直接定位到输入框
        details: dict[str, str] = {}
        for err in exc.errors():
            location = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
            details[location or "body"] = str(err.get("msg", "参数不合法"))
        body = fail(int(ErrorCode.PARAM_INVALID), "参数校验失败", {"fields": details}).model_dump(mode="json")
        log.info("validation_failed", fields=list(details.keys()))
        return _json(body, 422)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        message = str(exc.detail) if exc.detail else "请求失败"
        body = fail(int(code), message).model_dump(mode="json")
        return _json(body, exc.status_code)

    @app.exception_handler(IntegrityError)
    async def _handle_integrity_error(_request: Request, exc: IntegrityError) -> JSONResponse:
        """唯一约束/外键冲突 → 409，不泄露约束名（约束名会暴露表结构）。"""
        log.warning("db_integrity_error", error=str(exc.orig)[:300])
        body = fail(int(ErrorCode.OPERATION_CONFLICT), "数据冲突，请刷新后重试").model_dump(mode="json")
        return _json(body, 409)

    @app.exception_handler(SQLAlchemyError)
    async def _handle_db_error(_request: Request, exc: SQLAlchemyError) -> JSONResponse:
        log.exception("db_error", error_type=type(exc).__name__)
        body = fail(int(ErrorCode.INTERNAL_ERROR), "数据服务暂时不可用").model_dump(mode="json")
        return _json(body, 503)

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        # 唯一一处记录完整堆栈的地方；trace_id 会一起进日志，便于按 ID 反查
        structlog.get_logger(__name__).exception("unhandled_exception", error_type=type(exc).__name__)
        body = fail(int(ErrorCode.INTERNAL_ERROR), "服务内部错误").model_dump(mode="json")
        return _json(body, 500)
