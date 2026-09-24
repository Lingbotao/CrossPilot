# CrossPilot

跨境电商**多平台运营中台**（SaaS，原生多租户）。

把 Amazon / Shopee / Lazada / TikTok Shop 的订单、库存、商品、财务、合规拉到一个中台里统一运营。产品壁垒集中在三件事：**Landed Cost（落地成本）引擎 + SKU 级利润核算 + 合规前置校验**。

> **当前阶段：M0 地基已完成**，正在进入 M1。本轮目标是「把开发交付版成品做出来」，上线相关的资质申请、生产发布、监控告警体系按规划 §13 后置。

| 项目 | 值 |
|---|---|
| 版本 | `0.1.0`（M0 完成） |
| 需求基线 | [`docs/CrossPilot-PRD-SRS-v1.0.md`](docs/CrossPilot-PRD-SRS-v1.0.md) —— **一切开发决策以它为准** |
| 执行计划 | [`docs/CrossPilot-开发规划-v1.0.md`](docs/CrossPilot-开发规划-v1.0.md)（内含 v1.1 修订） |
| 后端 | Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · Alembic · Pydantic v2 |
| 前端 | React 18 · TypeScript 5 · Vite 5 · Ant Design 5 · TanStack Query 5 · Zustand |
| 数据 | PostgreSQL 16（主库）· Redis 7（缓存/限流/锁/队列）· MinIO（对象存储） |
| 异步 | Celery 5 + Celery Beat |

---

## 目录

- [一、三条不可违背的架构铁律](#一三条不可违背的架构铁律)
- [二、仓库结构](#二仓库结构)
- [三、快速开始](#三快速开始)
- [四、常用命令](#四常用命令)
- [五、环境变量](#五环境变量)
- [六、测试策略](#六测试策略)
- [七、开发规范](#七开发规范)
- [八、API 约定](#八api-约定)
- [九、M0 完成度对照](#九m0-完成度对照)
- [十、已知限制与待办](#十已知限制与待办)
- [十一、文档索引](#十一文档索引)

---

## 一、三条不可违背的架构铁律

这三条**在 M0 就落地了**，不是「上线前再补」。事后补 = 全库返工 + 历史数据污染。

### 1. 多租户隔离「三道防线」（约束 C1）

数据串租户是灾难级事故（PRD 13.3），所以做成**默认安全（fail-closed）**，任何一道防线单独失效都不会漏数据：

| 防线 | 实现位置 | 拦住什么 |
|---|---|---|
| ① ORM 自动注入 | `backend/app/db/tenant_filter.py` | 日常 `select()` / 关系加载漏写 `tenant_id` |
| ② PostgreSQL RLS | `backend/alembic/versions/0001_*.py` | 有人绕过 ORM 裸写 SQL |
| ③ CI 越权测试 | `backend/tests/security/test_tenant_isolation.py` | 新增租户表忘了同步补用例 |

各防线具体行为：

| 场景 | 行为 |
|---|---|
| SELECT 租户表 | 自动追加 `tenant_id = <当前上下文>`；**无租户上下文 → 直接报错** |
| 关联 / 延迟加载 | 同样自动追加（`include_aliases=True`） |
| UPDATE / DELETE 租户表 | WHERE 里没有 `tenant_id` → **拒绝执行**（防「一条语句改全租户」） |
| INSERT 租户表 | `before_flush` 自动填充当前 `tenant_id` |
| 非租户表（`tenant` / `sys_user` / 平台字典） | 完全不干预 |

**两个配套设计**：

- **双数据库角色**。`crosspilot_owner` 是表 owner（跑迁移、可绕 RLS），`crosspilot_app` 是运行时角色（`NOBYPASSRLS`，受 RLS 约束）。即使应用代码有 bug，也翻不出自己的租户。
- **逃生舱是显式的**。系统级任务（如 Celery 扫描全平台店铺）必须显式声明并留日志：
  ```python
  stmt = select(Shop).execution_options(**{SKIP_FLAG: True})   # SKIP_FLAG = "skip_tenant_filter"
  ```

### 2. 金额一律 Decimal，禁止 float（约束 C4）

| 层 | 做法 |
|---|---|
| 数据库 | `NUMERIC(20, 6)`，不用 `FLOAT`/`REAL` |
| Python | `Decimal`，禁 `float` |
| 传输 | **字符串**（如 `"12.340000"`），避免 JS `Number` 精度丢失 |
| 前端 | `src/utils/money.ts` 纯字符串算术，**支持超过 `2^53` 的大额**；`eslint.config.js` 用 `no-restricted-globals` 直接封杀 `parseFloat` |

> 为什么较真：汇率换算 + 分摊 + 累加会让浮点误差放大到「卖一单亏一单」的程度。金额一旦算错，用户对整套系统的信任就没了。

### 3. 成本 / 利润对一线角色默认不可见（规则 F4）

| 层 | 实现 |
|---|---|
| 后端单一事实源 | `app/core/permissions.py` 的 `COST_VISIBLE_ROLES` + `role_can_view_cost()` |
| 后端强制 | `require_cost_visibility()` 依赖项，接口层强制校验（**前端隐藏不作为安全边界**） |
| 前端兜底 | `<CostGuard>` 组件（`src/components/PermissionGuard.tsx`） |

---

## 二、仓库结构

```
CrossPilot/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── core/               # 跨层基础设施
│   │   │   ├── config.py       # pydantic-settings + validate_for_runtime() 启动自检
│   │   │   ├── context.py      # contextvars：trace_id / tenant_id / user_id / role_code
│   │   │   ├── permissions.py  # ★ 角色与权限点唯一事实源（含 F4 成本可见性）
│   │   │   ├── errors.py       # ErrorCode 10xxx–80xxx + AppError 体系
│   │   │   ├── response.py     # 统一响应信封 ApiResponse[T]
│   │   │   ├── security.py     # bcrypt 口令 + JWT 双 Token
│   │   │   ├── crypto.py       # ★ 平台凭证 AES-256-GCM 字段级加密
│   │   │   ├── deps.py         # get_identity() / require_permission() 依赖项
│   │   │   ├── middleware.py   # TraceIdMiddleware（ULID）
│   │   │   ├── handlers.py     # 全局异常 → 统一信封
│   │   │   ├── logging.py      # structlog（与 stdlib 共管道，避免双重转义）
│   │   │   └── pagination.py   # 页码 / 游标分页
│   │   ├── db/
│   │   │   ├── base.py         # 命名约定 + PK/Tenant/Audit/SoftDelete 四个 mixin
│   │   │   ├── session.py      # 异步引擎 + apply_rls_tenant()
│   │   │   ├── tenant_filter.py# ★ 防线①：ORM 自动注入 + 批量写 fail-closed
│   │   │   └── snowflake.py    # Snowflake ID（BIGINT，不可枚举）
│   │   ├── models/             # tenant.py(8 表) / audit.py(日志+分区) / enums.py
│   │   ├── schemas/            # Pydantic v2 出入参
│   │   ├── repositories/       # ★ 唯一允许触达数据库的层
│   │   ├── services/           # 业务逻辑（可纯单测，不起数据库）
│   │   ├── api/                # health.py(根路径探针) + v1/{auth,tenants}.py
│   │   ├── adapters/           # ★ 平台适配器层（M1 起填充）
│   │   ├── engines/            # ★ Landed Cost / 利润 / 合规引擎（M3–M4）
│   │   ├── tasks/              # Celery：celery_app.py / sync.py / batch.py / report.py
│   │   ├── webhooks/           # 平台回调入口
│   │   └── main.py             # create_app() 应用工厂
│   ├── alembic/versions/       # 迁移（批次 1：租户权限域 8 表 + RLS + 分区）
│   ├── scripts/seed_dev.py     # 开发种子数据（拒绝在 prod 运行）
│   └── tests/                  # unit / integration / security / contract
│
├── frontend/                   # React + Vite 前端
│   ├── src/
│   │   ├── api/                # client.ts（拦截器/刷新单飞/幂等键）· types.ts · tokenStore.ts · auth.ts
│   │   ├── store/auth.ts       # zustand：登录态 + permissionSet + canViewCost
│   │   ├── hooks/              # usePermission.ts
│   │   ├── components/         # ★ MoneyText · PermissionGuard/CostGuard · AppLayout
│   │   ├── router/             # menu.tsx（带里程碑标记）· guards.tsx · index.tsx
│   │   ├── pages/              # LoginPage · DashboardPage(M0 自检页) · Placeholder · NotFound
│   │   ├── utils/              # ★ money.ts（纯字符串算术 + 单测）· format.ts · trace.ts
│   │   ├── i18n/               # zh-CN.ts（V1 仅中文界面）
│   │   ├── app/                # theme.ts · providers.tsx · feedback.ts
│   │   ├── App.tsx / main.tsx  # 入口
│   │   └── vite-env.d.ts
│   └── tools/link-bins.mjs     # 受限环境补 node_modules/.bin 软链（正常环境用不到）
│
├── infra/
│   ├── postgres/init/01-init.sql  # 创建 crosspilot_app 角色 + 自检（若是超级用户则报错）
│   └── nginx/default.conf         # 前端静态 + /api 反代
│
├── docs/                       # 需求 / 规划 / 资质指南
├── scripts/check_secrets.py    # 零依赖密钥扫描器（pre-commit + CI 共用）
├── .github/workflows/ci.yml    # 4 道 CI 门禁
├── dev.sh                      # ★ 本机直跑脚本（一条命令起数据库 + API + 前端）
├── docker-compose.yml          # 一键起全栈
├── Makefile                    # 所有命令的唯一入口
└── .env.example                # 环境变量样例（含每一项的用途说明）
```

运行时产物（已进 `.gitignore`，删掉即回到干净状态，不碰系统环境）：

| 目录 | 内容 |
|---|---|
| `.dev/` | `dev.sh` 的日志、PID、便携 PostgreSQL 数据目录 |
| `.devtools/` | `dev.sh` 下载的便携 PostgreSQL 二进制（约 30MB 包解压而来） |

---

## 三、快速开始

### 前置条件

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | ≥ 3.12 | 后端运行时 |
| Node.js | ≥ 20.11 | 前端构建 |
| Docker + Compose | 任意近期版本 | **可选**。没有 Docker 时用 `./dev.sh`，它自带一个便携 PostgreSQL 16 |

### 路径 A：`./dev.sh` 一条命令（推荐，不需要 Docker）

`dev.sh` 把「起数据库 → 迁移 → 种子 → API → 前端」串成一条命令，Ctrl+C 一起退：

```bash
./dev.sh
```

本机没有 PostgreSQL 时，它会明确告诉你要执行的一次性步骤：

```bash
./dev.sh db-install   # 下载便携 PostgreSQL 16（约 30MB），此后不再需要 Docker / brew
```

**它做的事**：体检 → 从 `.env.example` 生成 `.env`（随机 JWT / AES 密钥）→ 供给数据库（便携实例 / 已有实例 / Docker 三选一，自动探测）→ 建库建角色（复用容器那份 `01-init.sql`，含 RLS 前置自检）→ `alembic upgrade head` → 灌种子 → 起 API 与前端 → 两个日志聚合输出。

常用子命令（`./dev.sh help` 看全部）：

| 命令 | 作用 |
|---|---|
| `./dev.sh` | 默认：数据库 + 迁移 + 种子 + API + 前端 |
| `./dev.sh doctor` | 体检：工具链、`.env`、数据库、端口、迁移版本，**只读不改** |
| `./dev.sh status` | 进程 / 端口 / 健康探针状态 |
| `./dev.sh logs [api\|web\|pg]` | 跟踪日志（不带参数则同时跟 api + web） |
| `./dev.sh stop` | 停掉脚本启动的全部进程（含便携 PG） |
| `./dev.sh db` | 只处理数据库：供给 → 迁移 → 种子 |
| `./dev.sh db-reset` | 重建开发库（需输入 `yes` 确认） |
| `./dev.sh sql "SELECT 1"` | 直接查库（便携包不带 `psql`，用这个顶） |
| `./dev.sh test` / `lint` / `typecheck` | 质量门禁 |

产物只落在两处，且都已进 `.gitignore`：`.dev/`（日志、PID、PG 数据目录）、`.devtools/`（PG 二进制）。删掉即回到干净状态，不碰系统环境。

启动后：

| 入口 | 地址 |
|---|---|
| 前端 | http://localhost:5173 |
| API 文档（Swagger） | http://localhost:8000/docs |
| 健康探针 | http://localhost:8000/healthz · `/readyz` · `/version` |

演示账号（种子数据自动写入，**已验证可登录**）：

| 账号 | 角色 | 成本/利润可见 |
|---|---|---|
| `owner@example.com` | OWNER 所有者 | ✅ `can_view_cost=true`，26 个权限点 |
| `ops@example.com` | OPS_STAFF 运营专员 | ❌ `can_view_cost=false`，12 个权限点（连 `cost:read` 都没有） |

> 上面这一行就是 F4 的活体验证：登录后调 `/api/v1/auth/me` 对比两个账号的 `tenant.can_view_cost`。
> 密码是 `backend/scripts/seed_dev.py` 里的开发约定常量，该脚本会拒绝在 `APP_ENV=prod` 下运行。

### 路径 B：Docker Compose 起全栈

需要 Docker Desktop，好处是 PG/Redis/MinIO 一次全有（含 Celery worker / beat）：

```bash
make init          # 生成 .env + 装后端依赖 + 装前端依赖
# ⚠️ 编辑 .env，至少填 JWT_SECRET 与 CREDENTIAL_AES_KEY（生成命令见「环境变量」一节）
make up            # 起 PG + Redis + MinIO + migrate + api + worker + beat + web
make seed          # 首次起库后写种子数据
```

前端在 http://localhost:8080，MinIO 控制台在 http://localhost:9001。

### 路径 C：只用 Makefile

想逐步手动控制时（等价于 `dev.sh` 拆开执行）：

```bash
make backend-install && make frontend-install
make migrate       # 需要 PG
make seed
make api           # 终端 1：后端 → http://localhost:8000
make web           # 终端 2：前端 → http://localhost:5173（/api 自动代理到 8000）
```

**完全没有任何数据库**时仍可执行：

```bash
make test-unit     # 单元测试（SQLite 内存库，不需要 PG）
make build         # 前端构建
```

### 排障：前端依赖装不上

| 现象 | 原因 | 处理 |
|---|---|---|
| 拉元数据超时（`ETIMEDOUT`） | 默认源在国内不稳定 | 换镜像：`npm install --registry=https://registry.npmmirror.com` |
| `npm install` 报错在 `node_modules/.bin` 的**重命名/建链**上 | 受限环境（沙箱、企业代理）禁止 symlink 创建 | `npm install --no-bin-links` 装包，再 `node tools/link-bins.mjs` 补 `.bin` 软链 |

> `tools/link-bins.mjs` 只做一件事：读各包 `package.json` 的 `bin` 字段，在 `node_modules/.bin/` 下建等价软链。**正常环境不需要它**。

### 排障：启不起来（`unbound variable` / `command not found`）

| 现象 | 原因 | 处理 |
|---|---|---|
| `PG_PORT\ufffd: unbound variable` —— **变量名里带乱码** | bash 3.2（macOS 自带）在**多字节 locale** 下，`$VAR` 后紧跟全角字符会把该字符的首字节吸进变量名 | 脚本内已统一写成 `${VAR}`。**自己写 shell 时务必照做**：`"端口 $PORT）"` 必须写成 `"端口 ${PORT}）"` |
| `xxx: command not found` | 调用了不存在的函数名。`bash -n` 只查语法，**查不出这种错** | 按提示的函数名去仓库里搜定义，改正调用点 |

> **验证 shell 脚本时不要只信自己的终端。** `bash -n` 只做语法检查；而 `${}` 这类变量名问题**只在多字节 locale 下才现形**（`LC_CTYPE=C` 时完全正常，会给出虚假的通过）。改动 `dev.sh` 后请用 UTF-8 locale 走一遍真实路径：
>
> ```bash
> LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 ./dev.sh
> ```
>
> 这两类问题已有自动化拦截：`scripts/check_shell_locale.py`（pre-commit + CI 第零关）。

### 验证安装是否成功

```bash
curl -sS http://localhost:8000/healthz    # {"code":0,...,"data":{"status":"ok"}}
curl -sS http://localhost:8000/readyz     # 会真的探一次数据库
curl -sS http://localhost:8000/version
```

---

## 四、常用命令

所有命令统一走 `Makefile`（避免口头传递零散 shell 片段）：

```bash
make help
```

| 分类 | 命令 | 作用 |
|---|---|---|
| 初始化 | `make init` | 环境变量 + 前后端依赖一键装 |
| | `make env` | 从 `.env.example` 生成 `.env`（已存在则不覆盖） |
| 全栈 | `make up` / `make down` / `make logs` / `make ps` | Docker Compose 生命周期 |
| **一键直跑** | `./dev.sh`（= `make dev`） | **本机推荐**：自带便携 PG，起数据库 + 迁移 + 种子 + API + 前端 |
| | `./dev.sh doctor` / `status` / `logs` / `stop` | 体检 / 状态 / 日志 / 停止 |
| 逐条直跑 | `make api` / `make worker` / `make beat` / `make web` | 手动控制每一步，便于打断点 |
| 数据库 | `make migrate` | 应用全部未执行迁移 |
| | `make revision m="描述"` | 自动生成迁移 |
| | `make seed` | 写开发种子数据 |
| | `make reset-db` | ⚠️ 清库重跑（仅开发环境） |
| 质量 | `make lint` | ruff check --fix + format + eslint |
| | `make typecheck` | mypy + `tsc --noEmit` |
| | `make test` | 全量测试 + 覆盖率门禁（≥70%） |
| | `make check` | **CI 主链路**：lint → typecheck → test |
| | `make build` | 前端产物构建 |

### 提交前钩子

```bash
pre-commit install
```

`pre-commit` 在提交时自动跑：ruff（含格式化）、mypy、eslint、`tsc`、**密钥扫描**。全部刻意控制在 3 秒内 —— 钩子慢了就会被 `--no-verify` 绕过，那等于没有。

---

## 五、环境变量

完整清单与逐项说明见 [`.env.example`](.env.example)。**铁律：任何密钥/凭证一律走环境变量，禁止硬编码**（约束 C5 / NFR-S-02）。

最小可用配置（其余可留空）：

| 变量 | 说明 |
|---|---|
| `APP_ENV` | `dev` / `test` / `staging` / `prod`。**生产环境下启动自检会拒绝默认密钥** |
| `DATABASE_URL` | 运行时连接串，用 `crosspilot_app` 角色（受 RLS 约束） |
| `DATABASE_MIGRATION_URL` | 迁移连接串，用 `crosspilot_owner` 角色（表 owner） |
| `REDIS_URL` | 缓存 / 限流 / 锁 |
| `JWT_SECRET` | ≥ 32 字符随机串 |
| `CREDENTIAL_AES_KEY` | AES-256-GCM 主密钥 |
| `CORS_ORIGINS` | 允许的前端来源，逗号分隔 |

生成密钥：

```bash
# JWT_SECRET
python3 -c "import secrets;print(secrets.token_urlsafe(48))"

# CREDENTIAL_AES_KEY（必须是 urlsafe base64 编码的 32 字节）
python3 -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

**为什么有两个数据库连接串**：迁移需要改表结构（DDL），运行时绝不能有。用两个角色把权限切开——即使应用被注入，也改不了表结构。

启动自检（`settings.validate_for_runtime()`）会在 `APP_ENV=prod` 时检查：JWT 密钥不是默认值、AES 密钥长度正确、关键项非空。**把「缺密钥」从运行期错误提前成启动期失败**。

---

## 六、测试策略

分为三层，边界清晰：

| 层 | 位置 | 需要 PG | 说明 |
|---|---|---|---|
| 单元 | `tests/unit/` | ❌ | SQLite 内存库。覆盖错误码、权限矩阵、JWT、加密、分页、Snowflake、**ORM 租户过滤机制** |
| 集成 | `tests/integration/` | ✅ | 健康探针、鉴权守卫、错误契约、OpenAPI |
| 安全 | `tests/security/` | ✅ | ★ **RLS 越权用例**——PG 未就绪时自动 skip（标记 `needs_db`） |

```bash
make test-unit        # 无数据库可跑
make test-security    # 需要 PG
make test-integration # 需要 PG
make test             # 全量 + 覆盖率门禁 ≥70%
```

**当前基线**（本机无 Docker 环境下实测）：

```
121 passed, 7 skipped          # 7 个 skipped = 需要 PostgreSQL 的 RLS 越权用例
覆盖率 74%                      # 门禁 70%
ruff ✅  ruff format ✅  mypy ✅（48 个源文件零问题）
```

> ⚠️ 那 7 个 skip 是**防线②③的验证**。起上 PG 后务必执行 `make test-security` 让它全绿——不然等于第二、三道防线没验证过。

---

## 七、开发规范

### 分层铁律

```
api（只做参数校验与响应组装）
  └─ services（业务逻辑，禁止 import ORM 会话细节）
       └─ repositories（★ 唯一允许写 SQL 的地方）
```

好处：services 可以纯单测；租户条件与软删除过滤只写一次，不会「某个查询忘了带条件」。

### ★ 新增一张租户表的检查清单

漏一步就是数据串租户。**新增业务表必须全部勾完**：

- [ ] 模型继承 `PKMixin` + `TenantMixin`（+ `AuditMixin`；主数据再加 `SoftDeleteMixin`）
      —— 漏 `TenantMixin` 等于**漏隔离**，防线①和②都按「表里有没有 `tenant_id` 列」判断
- [ ] 注册到 `app/models/__init__.py` 的 `TENANT_SCOPED_TABLES`
- [ ] 生成迁移后，**手工在迁移里补 RLS 策略**（`autogenerate` 不会生成策略）
- [ ] 在 `tests/security/test_tenant_isolation.py` 补一条「A 租户读不到 B 租户数据」用例
- [ ] 用 `make test-security` 验证通过

### 其他约定

| 项 | 约定 |
|---|---|
| 主键 | BIGINT + Snowflake（不可枚举，别用自增） |
| 时间 | 全系统 UTC 存储，展示层再转时区 |
| 金额 | `NUMERIC(20,6)` ↔ `Decimal` ↔ **字符串传输** |
| 流水表 | 订单、库存流水、审计日志**禁止软删除**——删了就查不到，等于账不平 |
| 跨租户访问 | 返回 **404 而不是 403**——403 等于告诉攻击者「这个 ID 存在，只是不属于你」 |
| 税率/费率/限流配额 | 一律**配置化**（含生效日期 + 版本化 + 来源标注），**禁止硬编码** |
| 幂等键 | `{platform}:{shop_id}:{platform_order_id}` |
| 改同一文件 | 多处修改请用脚本一次性替换或串行编辑，**不要并行提交同一文件的多处 Edit**（会互相覆盖） |

### 日志与排障

任何响应都带 `trace_id`（响应头 `X-Trace-Id` + 响应体 + 日志三处一致）。排查问题时：

```bash
docker compose logs api | grep <trace_id>
```

---

## 八、API 约定

### 统一响应信封

所有接口一律返回：

```json
{
  "code": 0,
  "message": "ok",
  "data": {},
  "trace_id": "01JC...",
  "timestamp": "2026-09-23T12:00:00Z"
}
```

`code = 0` 表示成功，非 0 见错误码段位。

### 错误码段位

| 段位 | 领域 |
|---|---|
| `10xxx` | 通用 |
| `20xxx` | 认证与租户 |
| `30xxx` | 平台与同步 |
| `40xxx` | 商品 |
| `50xxx` | 订单 |
| `60xxx` | 库存 |
| `70xxx` | 财务 |
| `80xxx` | 合规 |

### 已实现接口（M0）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 存活探针（不碰数据库） |
| GET | `/readyz` | 就绪探针（真的探一次 DB） |
| GET | `/version` | 版本信息 |
| POST | `/api/v1/auth/login` | 登录，返回双 Token |
| POST | `/api/v1/auth/refresh` | 刷新 Access Token |
| POST | `/api/v1/auth/logout` | 登出 |
| GET | `/api/v1/auth/me` | 当前用户 + 租户 + 权限点 |
| GET | `/api/v1/tenants/current` | 当前租户上下文 |

> 健康探针刻意挂在**根路径**而不是 `/api/v1` 下：K8s probe、负载均衡、监控系统都按固定路径访问，不应随业务 API 版本一起演进。

### 鉴权与请求头

| 头 | 用途 |
|---|---|
| `Authorization: Bearer <access_token>` | 鉴权（Access 15 分钟 / Refresh 7 天） |
| `X-Trace-Id` | 可选，由客户端传入以串联前后端链路；不传则服务端生成 |
| `Idempotency-Key` | 写接口幂等键 |

---

## 九、M0 完成度对照

对照 [`docs/CrossPilot-开发规划-v1.0.md`](docs/CrossPilot-开发规划-v1.0.md) 的 M0 任务清单：

| ID | 任务 | 状态 | 落地位置 |
|---|---|---|---|
| M0-01 | 仓库与目录脚手架 | ✅ | 全仓库 + 本 README |
| M0-02 | `docker compose up` 一键起全栈 | ✅ | `docker-compose.yml`（9 个服务，含 healthcheck 与依赖顺序） |
| M0-03 | 后端基座：应用工厂 / 配置 / 统一响应与错误码 / structlog | ✅ | `app/main.py`、`app/core/*` |
| M0-04 | 数据库基线 + Alembic 批次 1（租户权限域 8 表） | ✅ | `alembic/versions/0001_*.py` |
| M0-05 | ★ 多租户三道防线 | ✅ | `db/tenant_filter.py` + 迁移内 RLS 策略 + `tests/security/` |
| M0-06 | JWT 双 Token + 租户上下文（contextvar） | ✅ | `core/security.py`、`core/context.py`、`core/deps.py` |
| M0-07 | CI/CD + pre-commit + 覆盖率门禁 | ✅ | `.github/workflows/ci.yml`（4 条管道）、`.pre-commit-config.yaml` |
| M0-08 | 最小可观测：`/healthz` + structlog 全链 trace | ✅ | `api/health.py`、`core/logging.py`、`core/middleware.py` |
| M0-09 | 前端脚手架 + 路由守卫 + 拦截器 + `MoneyText` + `PermissionGuard` | ✅ | `frontend/src/**` |
| M0-10 | 开发接入环境：域名 + HTTPS + **公网可达 OAuth 回调** | ⏳ | 配置项已就位（`OAUTH_REDIRECT_BASE_URL`），**待实际部署** |

批次 1 迁移含：8 张业务表 + **RLS 策略** + `audit_log` 按月的 **RANGE 分区（14 个月）** + `app_user_tenants()` SECURITY DEFINER 函数（登录时需要跨租户查成员关系，这是唯一合法例外）+ 授予运行时角色。

---

## 十、已知限制与待办

按规划 §1.4 的四类边界，当前**刻意未做**的事项：

| 项 | 原因 | 何时做 |
|---|---|---|
| 生产发布 / K8s 编排 | 本阶段只做开发交付版 | 规划 §13 |
| Prometheus + Grafana + OTel | M0-08 已释放 0.7 人日 | 规划 §13 |
| 平台服务商（Public/ISV）资质 | 开发期只需 Sandbox 档 | 规划 §13（见 [`docs/平台开发者资质申请指南.md`](docs/平台开发者资质申请指南.md)） |
| 官网 / 隐私政策 / 演示环境 | 资质申请材料，依赖线上产品证据 | 规划 §13 |
| 500 并发压测 / OWASP 全扫 | 验收口径已改为「单机冒烟 + 基本安全自查」 | 上线前 |

**需要留意的技术债（已记录，不是遗漏）**：

1. **Refresh Token 存在 `localStorage`**（`src/api/tokenStore.ts`）。存在 XSS 窃取面，文件内已注明风险，规划 §13 跟进改为 HttpOnly Cookie。
2. **7 个 RLS 越权用例在无 PG 环境下 skip**。起上 PG 必须跑 `make test-security`。
3. **`app/adapters/` 存在已知能力缺口（规划风险 N5）**：F11 所需的 `fetch_messages` / `reply_message` / `fetch_reviews` 未纳入适配器抽象。M1 起填充适配器时必须一并补上，否则业务层要返工。
4. **税率 / 关税 / 平台限流数值必须以官方来源复核**。代码里只放结构，真实数值走配置且在界面标注来源与更新日期。
5. **租户自助注册需要 `SECURITY DEFINER` 函数**。`tenant` 表的 `INSERT ... RETURNING` 会连带过 SELECT 策略，而注册期尚无租户上下文 —— 现在开发种子靠 owner 会话绕过（`app/db/session.py::owner_session_scope`）。M1 实现「租户注册」接口时必须补一个 `app_register_tenant()` 这类 SECURITY DEFINER 函数，否则运行时角色插不进去。
6. **`alembic check` 尚有 2 处模型/迁移差异**（`tenant.code` 的唯一约束、`user_data_scope` 的复合唯一约束列序）。属 autogenerate 的经典误报（unique index vs unique constraint），不影响运行，但会让下次 `make revision` 产出无意义迁移 —— 生成迁移后务必人工审阅 diff。
7. **便携 PostgreSQL 包不含 `psql`**。`dev.sh db-install` 装的是 server 端二进制，需要交互式查库时用 `./dev.sh sql "SELECT ..."`；容器模式（`make up`）则自带完整客户端工具。

---

## 十一、文档索引

| 文档 | 用途 |
|---|---|
| [`docs/CrossPilot-PRD-SRS-v1.0.md`](docs/CrossPilot-PRD-SRS-v1.0.md) | **需求基线**（17 章）。冲突时以它为准 |
| [`docs/CrossPilot-开发规划-v1.0.md`](docs/CrossPilot-开发规划-v1.0.md) | **执行计划**（v1.1 修订）。WBS / 人日 / 甘特 / 降级路径 / 上线前补做清单 |
| [`docs/平台开发者资质申请指南.md`](docs/平台开发者资质申请指南.md) | 四平台资质要求对比、材料清单、提交顺序、踩坑清单 |
| [`docs/CrossPilot-V2.0-功能候选清单.md`](docs/CrossPilot-V2.0-功能候选清单.md) | V2 候选功能（V1 明确不做） |
| [`docs/overview.md`](docs/overview.md) | 需求文档的交付说明与阅读指引 |

### 范围红线（V1 不做）

自建 C 端商城 · 自研 WMS · 物流轨迹全链路 · 广告自动投放/调价 · AI 全流程生成 Listing · 移动端 App · 支付通道直连 · 非中文界面。

### 合规红线（产品责任边界）

系统**不提供**任何帮助用户刷单、刷评、低报申报价值、规避账号关联的功能（PRD 12.3）。机翻内容必须显性可见，不提供「一键机翻后直接全站发布」路径。
