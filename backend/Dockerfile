# =============================================================================
# 后端镜像
#
# 目标：镜像里**不含**开发依赖、不以 root 运行、带健康检查。
# 说明：基础镜像固定 3.12 —— 与 pyproject.toml 的 requires-python 对齐，
#       避免"本机 3.13 能跑、容器 3.12 报语法错误"这类环境漂移。
# =============================================================================
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC

WORKDIR /app

# curl 用于容器健康检查；装完立刻清 apt 缓存，别把 200MB 垃圾留在镜像里
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用层缓存：改业务代码时不会重装依赖）
COPY pyproject.toml ./
COPY app ./app
RUN pip install .

# 迁移脚本与种子脚本不打进包，但要能被 alembic / scripts 引用
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts

# 非 root 运行：容器逃逸后的权限边界
RUN useradd --create-home --uid 10001 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 用存活探针做健康检查，不用 /readyz —— 依赖抖动不该导致容器被重建
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
