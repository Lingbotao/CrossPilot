"""健康检查与可观测入口（M0-08）。

区分两个探针是有意的，别合并：

- ``/healthz``（liveness）：只证明**进程活着**。绝不能查数据库 ——
  否则数据库抖动会导致 K8s 把正常的 Pod 反复杀掉重启，把小故障放大成雪崩。
- ``/readyz``（readiness）：检查**依赖是否可用**。不通时从负载均衡摘掉，
  但不重启进程 —— 等数据库恢复后自动重新接入。
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app import __version__
from app.core.config import settings
from app.core.errors import ErrorCode
from app.core.response import ApiResponse, fail, ok
from app.db.session import check_db_health

router = APIRouter(tags=["系统"])

_STARTED_AT = time.time()


class HealthData(BaseModel):
    status: str
    version: str
    env: str
    uptime_seconds: int


class ReadinessData(BaseModel):
    status: str
    checks: dict[str, bool]
    latency_ms: dict[str, int | None]


@router.get("/healthz", response_model=ApiResponse[HealthData], summary="存活探针")
async def healthz() -> ApiResponse[HealthData]:
    return ok(
        HealthData(
            status="ok",
            version=__version__,
            env=settings.app_env,
            uptime_seconds=int(time.time() - _STARTED_AT),
        )
    )


@router.get("/readyz", response_model=ApiResponse[ReadinessData], summary="就绪探针")
async def readyz(response: Response) -> ApiResponse[ReadinessData] | ApiResponse[dict[str, object]]:
    db_ok, db_latency = await check_db_health()

    checks = {"database": db_ok}
    latencies: dict[str, int | None] = {"database": db_latency}

    ready = all(checks.values())
    if not ready:
        # 503 让负载均衡摘掉本实例；响应体仍走统一信封，便于人工排查
        response.status_code = 503
        return fail(
            int(ErrorCode.INTERNAL_ERROR),
            "依赖不可用",
            {"checks": checks, "latency_ms": latencies},
        )

    return ok(
        ReadinessData(
            status="ready",
            checks=checks,
            latency_ms=latencies,
        )
    )


@router.get("/version", response_model=ApiResponse[dict[str, str]], summary="版本信息")
async def version() -> ApiResponse[dict[str, str]]:
    return ok({"version": __version__, "env": settings.app_env, "api_prefix": settings.api_v1_prefix})
