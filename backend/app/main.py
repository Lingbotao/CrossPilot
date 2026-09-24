"""应用工厂与入口。

启动顺序有讲究（顺序错了会出现"日志没配置/配置没校验"的隐蔽问题）：

    1. 配置日志（之后所有日志才是结构化 JSON）
    2. 配置自检（生产环境缺密钥直接拒绝启动）
    3. 挂中间件与异常处理
    4. 注册路由

运行：``uvicorn app.main:app --reload``（或 ``make api``）
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse

from app import __version__
from app.api.health import router as health_router
from app.api.v1 import api_router
from app.core.config import settings
from app.core.handlers import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import TraceIdMiddleware
from app.db.session import dispose_engine
from app.models import TENANT_SCOPED_TABLES

log = get_logger(__name__)

DESCRIPTION = """
CrossPilot —— 跨境电商多平台运营中台 API。

**统一响应信封**：所有接口都返回 `{code, message, data, trace_id, timestamp}`，`code=0` 表示成功。

**错误码段位**：10xxx 通用 / 20xxx 认证与租户 / 30xxx 平台与同步 / 40xxx 商品 /
50xxx 订单 / 60xxx 库存 / 70xxx 财务 / 80xxx 合规。

**金额约定**：一律以字符串传输 Decimal（如 `"12.340000"`），避免 JS 精度丢失。

**排障方式**：任何响应都带 `trace_id`（同时出现在响应头 `X-Trace-Id` 与日志中），
用它可以直接检索到该请求的全链路日志。
"""


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    log.info(
        "app_started",
        version=__version__,
        env=settings.app_env,
        tenant_scoped_tables=len(TENANT_SCOPED_TABLES),
    )
    yield
    await dispose_engine()
    log.info("app_stopped")


def create_app() -> FastAPI:
    # 1) 日志：必须在任何 log 调用之前完成配置
    configure_logging()
    # 2) 配置自检：把"缺密钥"从运行期错误提前到启动期
    settings.validate_for_runtime()

    app = FastAPI(
        title="CrossPilot API",
        description=DESCRIPTION,
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "系统", "description": "健康探针、版本信息"},
            {"name": "认证", "description": "登录、刷新、登出、当前用户"},
            {"name": "租户", "description": "租户上下文、角色与权限点"},
            {"name": "成员与权限", "description": "成员邀请、角色和数据范围"},
            {"name": "审计", "description": "不可篡改的敏感操作审计日志"},
        ],
    )

    register_exception_handlers(app)

    # 中间件注册顺序 = 执行顺序的逆序：最后注册的最外层
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Trace-Id",
            "X-Confirmation-Token",
        ],
        expose_headers=["X-Trace-Id"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(TraceIdMiddleware)  # 最外层：保证 trace_id 覆盖包括 CORS 预检在内的所有请求

    # 健康探针挂在**根路径**（/healthz、/readyz、/version）而不是 /api/v1 下 ——
    # 探针是基础设施接口：K8s probe、负载均衡、监控系统都按固定路径访问，
    # 不应随业务 API 版本一起演进。
    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def _root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    return app


app = create_app()
