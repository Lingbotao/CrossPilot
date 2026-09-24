# =============================================================================
# 前端镜像：多阶段构建（构建产物用 nginx 托管）
#
# 为什么分两段：node 镜像 ~1GB，nginx 镜像 ~50MB。
# 单阶段构建会把整个 node_modules 带进最终镜像，属于典型的"镜像臃肿"。
# =============================================================================

# ---------------------------------------------------------------- 构建阶段
FROM node:22-alpine AS builder

WORKDIR /app

# 先只拷贝依赖清单，利用层缓存：改业务代码时不会重新 npm install
COPY package.json package-lock.json* ./
# 有 lock 文件用 npm ci（可复现）；没有则退回 npm install
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY tsconfig.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src

# 构建期注入的变量：Vite 会把 import.meta.env.VITE_* 静态替换进产物，
# 所以这些值在**构建时**就固化了（改后端地址必须重新构建镜像，不能只改环境变量）
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

# build 脚本本身已经包含 `tsc -b`：类型不过就不出产物，避免"能跑但类型是错的"
RUN npm run build

# ---------------------------------------------------------------- 运行阶段
FROM nginx:1.27-alpine AS runtime

# SPA 路由回退 + API 反向代理配置
COPY --from=builder /app/dist /usr/share/nginx/html

# 注意：nginx 配置**不在这里 COPY** —— 它是运维配置，唯一事实来源放在
# `infra/nginx/default.conf`，由 docker-compose 以只读卷挂载进来。
# 这样"改一次配置要同步改两处"的问题就不存在了。
# 直接 `docker build` 而不经 compose 时，会使用 nginx 镜像自带的默认配置
# （能提供静态文件，但 SPA 深链接会 404）—— 请走 `make up`。

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
