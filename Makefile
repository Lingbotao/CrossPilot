# =============================================================================
# CrossPilot 常用命令入口
# 约定：所有命令通过本文件暴露，避免口头传递零散 shell 片段
# =============================================================================
SHELL := /bin/bash
BACKEND := backend
FRONTEND := frontend
PY := $(BACKEND)/.venv/bin/python
PIP := $(BACKEND)/.venv/bin/pip

.DEFAULT_GOAL := help
.PHONY: help init env backend-install frontend-install up down logs ps \
        migrate revision seed api worker beat web test test-unit test-security \
        test-integration lint format typecheck check build clean reset-db \
        dev dev-stop dev-doctor

help: ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- 初始化
init: env backend-install frontend-install ## 一键初始化开发环境（首次执行）

env: ## 从样例生成 .env（已存在则不覆盖）
	@test -f .env || (cp .env.example .env && echo "已生成 .env，请填入密钥")
	@test -f $(BACKEND)/.env || cp .env $(BACKEND)/.env 2>/dev/null || true

backend-install: ## 安装后端依赖并建虚拟环境
	@test -d $(BACKEND)/.venv || python3 -m venv $(BACKEND)/.venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e "$(BACKEND)[dev]"

frontend-install: ## 安装前端依赖
	cd $(FRONTEND) && npm install

# ---------------------------------------------------------------- 全栈运行
up: ## 一键起全栈（PG + Redis + MinIO + api + worker + beat + web）
	docker compose up -d --build
	@echo "Web: http://localhost:8080  API 文档: http://localhost:8000/docs"

down: ## 停止全栈
	docker compose down

logs: ## 跟踪全栈日志
	docker compose logs -f --tail=100

ps: ## 查看容器状态
	docker compose ps

# ---------------------------------------------------------------- 本机直跑（不需要 Docker，推荐）
dev: ## 一键起本机开发环境（数据库+迁移+种子+API+前端）—— 详见 ./dev.sh
	./dev.sh up

dev-stop: ## 停止 ./dev.sh 启动的全部进程（含便携 PostgreSQL）
	./dev.sh stop

dev-doctor: ## 体检本机开发环境（只读，不改动任何文件）
	./dev.sh doctor

# ---------------------------------------------------------------- 本地直跑（逐条手动）
api: ## 本机直跑 API（热重载）
	cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker: ## 本机直跑 Celery Worker
	cd $(BACKEND) && .venv/bin/celery -A app.tasks.celery_app:celery_app worker -l info

beat: ## 本机直跑 Celery Beat
	cd $(BACKEND) && .venv/bin/celery -A app.tasks.celery_app:celery_app beat -l info

web: ## 本机直跑前端
	cd $(FRONTEND) && npm run dev

# ---------------------------------------------------------------- 数据库
migrate: ## 执行全部未应用的迁移
	cd $(BACKEND) && .venv/bin/alembic upgrade head

revision: ## 生成新迁移：make revision m="描述"
	cd $(BACKEND) && .venv/bin/alembic revision --autogenerate -m "$(m)"

seed: ## 写入开发种子数据（演示租户 + 管理员 + 内置角色）
	cd $(BACKEND) && .venv/bin/python -m scripts.seed_dev

reset-db: ## ⚠️ 清库重跑迁移（仅限开发环境）
	cd $(BACKEND) && .venv/bin/alembic downgrade base && .venv/bin/alembic upgrade head

# ---------------------------------------------------------------- 质量门禁
lint: ## 静态检查 + 自动格式化（后端 ruff / 前端 eslint）
	cd $(BACKEND) && .venv/bin/ruff check --fix app tests scripts alembic
	cd $(BACKEND) && .venv/bin/ruff format app tests scripts alembic
	$(BACKEND)/.venv/bin/ruff check --fix scripts   # 根级 CLI 脚本走自己的 ruff.toml
	$(BACKEND)/.venv/bin/ruff format scripts
	cd $(FRONTEND) && npm run lint --if-present

format: lint ## lint 的别名

typecheck: ## 类型检查（后端 mypy / 前端 tsc --noEmit）
	cd $(BACKEND) && .venv/bin/mypy app
	cd $(FRONTEND) && npx tsc --noEmit

test-unit: ## 单元测试（不需要数据库）
	cd $(BACKEND) && .venv/bin/pytest tests/unit -q

test-security: ## ★ 多租户越权测试（需要 PostgreSQL）
	cd $(BACKEND) && .venv/bin/pytest tests/security -q -m "not needs_db or needs_db"

test-integration: ## 集成测试（需要 PostgreSQL）
	cd $(BACKEND) && .venv/bin/pytest tests/integration -q

test: ## 全量后端测试（含覆盖率门禁 ≥70%）
	cd $(BACKEND) && .venv/bin/pytest --cov=app --cov-report=term-missing --cov-fail-under=70

check: lint typecheck test ## CI 三关：lint → type → test

build: ## 构建前端产物
	cd $(FRONTEND) && npm run build

clean: ## 清理构建产物与缓存
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache $(BACKEND)/.mypy_cache $(FRONTEND)/dist
