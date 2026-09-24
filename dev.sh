#!/usr/bin/env bash
# =============================================================================
# CrossPilot 本机直跑脚本（macOS / Linux，不需要 Docker）
#
# 一条命令把「数据库 + API + 前端」拉起来，Ctrl+C 一起退。
#
#   ./dev.sh              体检 → 准备 .env → 确保数据库 → 迁移 → 起 API + Web
#   ./dev.sh doctor       只体检，不改动任何东西
#   ./dev.sh stop         停掉本脚本起的所有进程
#   ./dev.sh help         全部命令
#
# 与本文件的约定：
#   1. 所有产物落在 .dev/（日志、PID、PG 数据）与 .devtools/（PG 二进制），
#      两者都已进 .gitignore，删掉即回到干净状态。
#   2. 不碰系统环境：不装 brew、不改 PATH、不写系统目录。
#   3. 兼容 macOS 自带的 bash 3.2 —— 不使用 bash4 语法（关联数组、${v,,} 等）。
# =============================================================================
set -euo pipefail

# 环境净化：让子进程只认项目自己的 venv / node_modules。
# PYTHONPATH 被父进程（IDE、版本管理器、其他工具链）污染时，venv 的隔离会静默失效，
# 后端可能 import 到项目外的同名模块 —— 这类问题排查起来极费时间，这里直接断掉。
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP 2>/dev/null || true

# ---------------------------------------------------------------- 路径
ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
DEV_DIR="$ROOT/.dev"
LOG_DIR="$DEV_DIR/logs"
RUN_DIR="$DEV_DIR/run"
PG_HOME="$ROOT/.devtools/pg"
PG_DATA="$DEV_DIR/localdb"
ENV_FILE="$ROOT/.env"
INIT_SQL="$ROOT/infra/postgres/init/01-init.sql"

# ---------------------------------------------------------------- 可调参数
PG_PORT="${CROSSPILOT_PG_PORT:-5432}"
DB_NAME="${CROSSPILOT_DB_NAME:-crosspilot}"
DB_OWNER="${CROSSPILOT_DB_OWNER:-crosspilot_owner}"
DB_OWNER_PWD="${CROSSPILOT_DB_OWNER_PWD:-crosspilot_owner_pwd}"
DB_APP="${CROSSPILOT_DB_APP:-crosspilot_app}"
DB_APP_PWD="${CROSSPILOT_DB_APP_PWD:-crosspilot_app_pwd}"
API_HOST="${CROSSPILOT_API_HOST:-127.0.0.1}"
API_PORT="${CROSSPILOT_API_PORT:-8000}"
WEB_PORT="${CROSSPILOT_WEB_PORT:-5173}"
PG_VERSION="16.2.0"
PG_MIRROR="${CROSSPILOT_PG_MIRROR:-https://maven.aliyun.com/repository/central}"

# ---------------------------------------------------------------- 输出
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_DIM=''; C_BOLD=''; C_OFF=''
fi

step() { printf '%s▸%s %s\n' "$C_BLUE$C_BOLD" "$C_OFF" "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
warn() { printf '%s!%s %s\n' "$C_YELLOW" "$C_OFF" "$*" >&2; }
err()  { printf '%s✗%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; }
dim()  { printf '%s  %s%s\n' "$C_DIM" "$*" "$C_OFF"; }
die()  { err "$*"; exit 1; }

# ---------------------------------------------------------------- 基础工具
have() { command -v "$1" >/dev/null 2>&1; }

port_listening() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

# 谁占着端口（用于报错时给出可执行的下一步）
port_owner() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1" (pid "$2")"}' || true
}

pid_of() { # $1=name
  local f="$RUN_DIR/$1.pid"
  [ -f "$f" ] && cat "$f" 2>/dev/null || true
}

pid_alive() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# 递归杀掉整个进程树。
# uvicorn --reload 会派生 reloader/worker 子进程，vite 也会派生 esbuild ——
# 只 kill 父进程会留下占着端口的孤儿。
kill_tree() {
  local pid="$1" sig="${2:-TERM}" kid
  [ -n "$pid" ] || return 0
  for kid in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_tree "$kid" "$sig"
  done
  kill -"$sig" "$pid" 2>/dev/null || true
}

wait_port() { # $1=port $2=seconds
  local p="$1" t="$2" i=0
  while [ "$i" -lt "$t" ]; do
    port_listening "$p" && return 0
    i=$((i + 1)); sleep 1
  done
  return 1
}

wait_http() { # $1=url $2=seconds
  local url="$1" t="$2" i=0
  while [ "$i" -lt "$t" ]; do
    curl -fsS -o /dev/null --max-time 2 "$url" 2>/dev/null && return 0
    i=$((i + 1)); sleep 1
  done
  return 1
}

ensure_dirs() {
  mkdir -p "$LOG_DIR" "$RUN_DIR"
}

# ---------------------------------------------------------------- 运行时探测
VENV_PY=""
detect_python() {
  if [ -x "$BACKEND/.venv/bin/python" ]; then
    VENV_PY="$BACKEND/.venv/bin/python"
  else
    err "后端虚拟环境不存在：backend/.venv"
    dim "修复：make backend-install"
    return 1
  fi
}

NPM_BIN=""
detect_node() {
  if have npm; then
    NPM_BIN="$(command -v npm)"
  elif [ -x /Users/botao/.workbuddy/binaries/node/versions/22.22.2-2/bin/npm ]; then
    NPM_BIN="/Users/botao/.workbuddy/binaries/node/versions/22.22.2-2/bin/npm"
  else
    err "找不到 npm"
    dim "修复：安装 Node 20+ 后重试，或设置 PATH"
    return 1
  fi
}

# ---------------------------------------------------------------- PG 定位
# 返回可用的 pg_ctl 绝对路径；找不到返回空
find_pg_ctl() {
  if [ -x "$PG_HOME/bin/pg_ctl" ]; then
    echo "$PG_HOME/bin/pg_ctl"; return 0
  fi
  if have pg_ctl; then
    command -v pg_ctl; return 0
  fi
  # Postgres.app（GUI 安装的常见位置）
  local app_bin
  for app_bin in /Applications/Postgres.app/Contents/Versions/*/bin/pg_ctl; do
    if [ -x "$app_bin" ]; then echo "$app_bin"; return 0; fi
  done
  return 1
}

pg_bin_dir() { dirname "$(find_pg_ctl)" 2>/dev/null || true; }

# PG 能否接受连接（走真实驱动，避免误判）
pg_ready() {
  ( cd "$BACKEND" && "$VENV_PY" - "$@" <<'PY' 2>/dev/null
import asyncio, sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def main() -> None:
    eng = create_async_engine(sys.argv[1], connect_args={"timeout": 3})
    try:
        async with eng.connect() as c:
            await c.execute(text("SELECT 1"))
    finally:
        await eng.dispose()

try:
    asyncio.run(main())
except Exception:
    raise SystemExit(1)
PY
  )
}

# 读 .env 里的某个键（不 source 整个文件 —— 值里可能有空格/特殊字符）
env_get() {
  [ -f "$ENV_FILE" ] || return 0
  grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true
}

# ---------------------------------------------------------------- .env
gen_secret() { "$VENV_PY" -c "import secrets; print(secrets.token_urlsafe(48))"; }
gen_aes_key() { "$VENV_PY" -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"; }

cmd_env() {
  ensure_dirs
  detect_python
  if [ -f "$ENV_FILE" ]; then
    ok ".env 已存在，保持不变（要重建请先删除它）"
    return 0
  fi
  [ -f "$ROOT/.env.example" ] || die "找不到 .env.example，无法生成 .env"

  local jwt aes tmp
  jwt="$(gen_secret)"
  aes="$(gen_aes_key)"

  # 用 python 改写：只在 KEY= 为空或为占位值时填随机值，其余原样保留
  tmp="$ENV_FILE.tmp.$$"
  "$VENV_PY" - "$ROOT/.env.example" "$tmp" "$jwt" "$aes" "$PG_PORT" "$DB_OWNER" "$DB_OWNER_PWD" "$DB_APP" "$DB_APP_PWD" "$DB_NAME" <<'PY'
import re, sys

src, dst, jwt, aes, pg_port, owner, owner_pwd, app, app_pwd, dbname = sys.argv[1:11]

# 行内注释要单独处理：.env.example 里写的是 `KEY=value   # 注释`，
# 这种写法交给 dotenv 解析会把注释当成值的一部分，所以这里统一剥掉。
def split_inline_comment(line: str) -> tuple[str, str]:
    out, in_s, in_d, i = [], False, False, 0
    while i < len(line):
        ch = line[i]
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == '#' and not in_s and not in_d and (i == 0 or line[i - 1] in ' \t'):
            return line[:i].rstrip(), line[i:]
        out.append(ch); i += 1
    return line.rstrip(), ''

overrides = {
    'JWT_SECRET': jwt,
    'CREDENTIAL_AES_KEY': aes,
    'DATABASE_URL': f'postgresql+asyncpg://{app}:{app_pwd}@localhost:{pg_port}/{dbname}',
    'DATABASE_MIGRATION_URL': f'postgresql+asyncpg://{owner}:{owner_pwd}@localhost:{pg_port}/{dbname}',
}

lines = []
for raw in open(src, encoding='utf-8').read().splitlines():
    if not raw.strip() or raw.lstrip().startswith('#'):
        lines.append(raw); continue
    if '=' not in raw:
        lines.append(raw); continue
    body, comment = split_inline_comment(raw)
    key, _, val = body.partition('=')
    key = key.strip()
    if key in overrides:
        val = overrides[key]
    if comment:
        lines.append(f'{key}={val}   {comment}')
    else:
        lines.append(f'{key}={val}')

open(dst, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
PY
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  ok "已生成 .env（JWT_SECRET 与 CREDENTIAL_AES_KEY 为随机值，文件权限 600）"
  dim "平台 App Key 等仍需你手工填写（M1 阶段才用得到）"
}

# ---------------------------------------------------------------- 便携 PG
pg_artifact() {
  local arch
  arch="$(uname -m)"
  case "$arch" in
    arm64) echo "embedded-postgres-binaries-darwin-arm64v8" ;;
    x86_64) echo "embedded-postgres-binaries-darwin-amd64" ;;
    *) die "不支持的 CPU 架构：${arch}（请改用 Docker：make up）" ;;
  esac
}

cmd_db_install() {
  ensure_dirs
  local art jar url want have_size
  art="$(pg_artifact)"
  jar="$ROOT/.devtools/${art}.jar"
  url="$PG_MIRROR/io/zonky/test/postgres/$art/$PG_VERSION/$art-$PG_VERSION.jar"

  if [ -x "$PG_HOME/bin/pg_ctl" ]; then
    ok "便携 PostgreSQL 已就绪：$PG_HOME"
    return 0
  fi

  mkdir -p "$ROOT/.devtools"
  # 幂等：先比对远端 content-length，已下完整就不重复拉（30MB 拉两遍很浪费）
  want="$(curl -fsSLI --max-time 30 "$url" 2>/dev/null | awk 'tolower($1)=="content-length:" {print $2}' | tr -d '\r' | tail -1)"
  have_size="$(wc -c <"$jar" 2>/dev/null | tr -d ' ' || echo 0)"
  if [ -n "$want" ] && [ "$want" = "$have_size" ]; then
    ok "安装包已完整（$((want / 1048576))MB），跳过下载"
  else
    step "下载 PostgreSQL $PG_VERSION 二进制（约 30MB，可断点续传）"
    dim "源：$PG_MIRROR"
    if ! curl -fSL -C - --retry 5 --retry-delay 3 -o "$jar" "$url"; then
      # 416 = 服务端认为本地已完整；交给后面的解压去判断真伪
      code="$(curl -sS -o /dev/null -w '%{http_code}' -C - "$url" 2>/dev/null || echo "")"
      [ "$code" = "416" ] \
        || die "下载失败。可换源重试：CROSSPILOT_PG_MIRROR=https://repo1.maven.org/maven2 ./dev.sh db-install"
    fi
  fi

  step "解压"
  # jar 里是一份 .txz（xz 压缩的 tar），先取出再展开
  ( cd "$ROOT/.devtools" && rm -rf pgtmp && mkdir pgtmp && cd pgtmp \
    && unzip -oq "$jar" \
    && txz="$(ls ./*.txz 2>/dev/null | head -1)" \
    && [ -n "$txz" ] || {
      # 极少数镜像会把 txz 再压一层，兜底直接找
      txz="$(find . -name '*.txz' | head -1)"
    }
    [ -n "$txz" ] || { echo "包内未找到 .txz"; exit 1; }
    # 用 python 解 xz：不依赖 bsdtar 是否编入 xz 支持
    "$VENV_PY" -c "
import tarfile, sys
with tarfile.open(sys.argv[1], 'r:xz') as t:
    t.extractall(sys.argv[2], filter='data')
" "$txz" extracted \
  ) || die "解压失败"

  # 包内可能是 ./bin 或 ./pgsql/bin，两种都认
  if [ -x "$ROOT/.devtools/pgtmp/extracted/bin/pg_ctl" ]; then
    rm -rf "$PG_HOME"; mv "$ROOT/.devtools/pgtmp/extracted" "$PG_HOME"
  elif [ -x "$ROOT/.devtools/pgtmp/extracted/pgsql/bin/pg_ctl" ]; then
    rm -rf "$PG_HOME"; mv "$ROOT/.devtools/pgtmp/extracted/pgsql" "$PG_HOME"
  else
    err "解压结果里找不到 bin/pg_ctl，包结构可能变了"
    dim "解压目录：$ROOT/.devtools/pgtmp/extracted"
    exit 1
  fi
  rm -rf "$ROOT/.devtools/pgtmp"

  # zip 不保留可执行位，必须补上
  chmod +x "$PG_HOME"/bin/* 2>/dev/null || true
  [ -x "$PG_HOME/bin/pg_ctl" ] || die "pg_ctl 不可执行：$PG_HOME/bin/pg_ctl"
  ok "便携 PostgreSQL 安装完成：$PG_HOME"
  dim "之后 ./dev.sh 会自动使用它，不需要 Docker / brew"
}

pg_running() { # 便携实例是否在跑
  [ -f "$PG_DATA/postmaster.pid" ] || return 1
  local pid
  pid="$(head -1 "$PG_DATA/postmaster.pid" 2>/dev/null || true)"
  pid_alive "$pid"
}

pg_start() {
  pg_running && return 0
  local ctl
  ctl="$(find_pg_ctl)" || die "找不到 pg_ctl"
  mkdir -p "$RUN_DIR"
  step "启动 PostgreSQL（端口 ${PG_PORT}，数据目录 .dev/localdb）"
  "$ctl" -D "$PG_DATA" -l "$LOG_DIR/pg.log" \
    -o "-p $PG_PORT -k '$RUN_DIR' -c listen_addresses=127.0.0.1" \
    -w -t 30 start >/dev/null 2>&1 \
    || { err "PostgreSQL 启动失败，日志尾部："; tail -20 "$LOG_DIR/pg.log" 2>/dev/null >&2 || true; exit 1; }
  ok "PostgreSQL 已启动"
}

pg_stop() {
  local ctl
  ctl="$(find_pg_ctl 2>/dev/null)" || return 0
  pg_running || return 0
  "$ctl" -D "$PG_DATA" -m fast -w -t 20 stop >/dev/null 2>&1 || true
}

# 初始化便携实例（仅首次）
pg_init_local() {
  [ -d "$PG_DATA" ] && [ -f "$PG_DATA/PG_VERSION" ] && return 0
  # 上次 initdb 中途失败会留下半成品目录，留着会让 initdb 直接拒绝执行。
  # 挪走而不是删除 —— 万一里面有值得看的东西，还在 .dev/ 里能找到。
  if [ -d "$PG_DATA" ]; then
    warn "检测到未完成的数据库目录，挪走后重建"
    mv "$PG_DATA" "$PG_DATA.broken.$$" 2>/dev/null \
      || die "无法挪走 ${PG_DATA}，请手工处理后重试"
    dim "已挪至 $PG_DATA.broken.$$"
  fi
  [ -x "$PG_HOME/bin/initdb" ] || die "便携 PostgreSQL 未安装。先执行：./dev.sh db-install"

  step "初始化数据库集群（首次，约 10 秒）"
  mkdir -p "$DEV_DIR"
  # owner 既是 PG 超级用户又是表 owner：
  #   - 超级用户 → 可建库、建角色、跑迁移
  #   - 表 owner → 默认绕过 RLS，正是迁移/种子脚本需要的姿态
  # 运行时账号 crosspilot_app 由 01-init.sql 单独创建，明确 NOBYPASSRLS。
  #
  # 必须用 --pwfile 给超级用户设密码：host 走 scram 认证，initdb 默认不设密码，
  # 漏掉这一步会得到 "password authentication failed for user crosspilot_owner"。
  local pwfile="$DEV_DIR/.pg-init-pw"
  printf '%s\n' "$DB_OWNER_PWD" >"$pwfile"
  chmod 600 "$pwfile"
  local rc=0
  "$PG_HOME/bin/initdb" -D "$PG_DATA" -U "$DB_OWNER" \
    --encoding=UTF8 --locale=C \
    --auth-local=trust --auth-host=scram-sha-256 \
    --pwfile="$pwfile" \
    >"$LOG_DIR/pg-initdb.log" 2>&1 || rc=$?
  rm -f "$pwfile"
  if [ "$rc" -ne 0 ]; then
    err "initdb 失败，详见 .dev/logs/pg-initdb.log"
    tail -20 "$LOG_DIR/pg-initdb.log" >&2
    exit 1
  fi
  ok "数据库集群初始化完成"
}

# 建库 + 建角色 + 授权 + RLS 前置自检（幂等）
#
# 注意：这里**不用 psql**。便携包的 bin/ 里只有 server 端程序（postgres/initdb/pg_ctl），
# 没有客户端工具。直接用 venv 里现成的 asyncpg 连过去执行，省掉一个外部依赖。
# 执行的 SQL 仍是容器那份 infra/postgres/init/01-init.sql —— 两条启动路径必须同一口径。
pg_provision() {
  detect_python
  [ -f "$INIT_SQL" ] || die "找不到 $INIT_SQL"

  step "初始化数据库、角色与 RLS 策略"
  "$VENV_PY" - "$DB_OWNER" "$DB_OWNER_PWD" "$PG_PORT" "$DB_NAME" "$INIT_SQL" \
    >"$LOG_DIR/pg-init.log" 2>&1 <<'PY' \
    || { err "初始化失败（RLS 前置条件未通过时会拦在这里，这正是期望行为）："; tail -25 "$LOG_DIR/pg-init.log" >&2; exit 1; }
import asyncio
import pathlib
import sys

import asyncpg

owner, password, port, dbname, init_sql_path = sys.argv[1:6]
kw = {"user": owner, "password": password, "host": "127.0.0.1", "port": int(port)}


async def main() -> None:
    # 1) 建库（CREATE DATABASE 不能跑在事务里，只能单条发）
    admin = await asyncpg.connect(database="postgres", **kw)
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            await admin.execute(f'CREATE DATABASE "{dbname}" OWNER "{owner}"')
            print(f"created database: {dbname}")
        else:
            print(f"database exists: {dbname}")
    finally:
        await admin.close()

    # 2) 角色 / 权限 / 自检：整份脚本一次发过去（PG 的 simple query 支持多语句，
    #    $$ 是服务端 dollar-quoting，不经过客户端参数解析）
    sql = pathlib.Path(init_sql_path).read_text(encoding="utf-8")
    conn = await asyncpg.connect(database=dbname, **kw)
    try:
        await conn.execute(sql)
        print("init sql applied")
    finally:
        await conn.close()


asyncio.run(main())
PY
  ok "数据库 $DB_NAME 与角色已就绪（RLS 前置检查通过）"
}

# 简易 SQL 执行：便携包没有 psql，用这个顶上（容器模式请直接用 psql）
pg_sql() {
  detect_python
  local stmt=""
  if [ "$#" -gt 0 ]; then
    stmt="$*"
  else
    printf '输入 SQL（EOF 结束）：\n'
    stmt="$(cat)"
  fi
  "$VENV_PY" - "$DB_OWNER" "$DB_OWNER_PWD" "$PG_PORT" "$DB_NAME" "$stmt" <<'PY'
import asyncio
import sys

import asyncpg

owner, password, port, dbname, stmt = sys.argv[1:6]


async def main() -> None:
    conn = await asyncpg.connect(
        user=owner, password=password, host="127.0.0.1", port=int(port), database=dbname
    )
    try:
        rows = await conn.fetch(stmt)
        if not rows:
            print("(无返回行)")
            return
        widths = [max(len(str(i)), max(len(str(r[i])) for r in rows)) for i in range(len(rows[0]))]
        print(" | ".join(str(k).ljust(w) for k, w in zip(rows[0].keys(), widths)))
        print("-+-".join("-" * w for w in widths))
        for r in rows:
            print(" | ".join(str(v).ljust(w) for v, w in zip(r.values(), widths)))
        print(f"({len(rows)} 行)")
    finally:
        await conn.close()


asyncio.run(main())
PY
}

# 决策树：把「本机没有 PG」这件事处理干净
provision_pg() {
  ensure_dirs
  detect_python

  # ① 你已显式指定外部库 → 直接用，不做任何供给
  local external="${CROSSPILOT_DATABASE_URL:-}"
  if [ -n "$external" ]; then
    if pg_ready "$external"; then
      ok "使用外部数据库：$(echo "$external" | sed -E 's#://[^@]*@#://***@#')"
      return 0
    fi
    die "CROSSPILOT_DATABASE_URL 指向的库连不上：$external"
  fi

  # ② 本脚本管理的便携实例
  if [ -x "$PG_HOME/bin/pg_ctl" ]; then
    pg_init_local
    pg_start
    if pg_ready "postgresql+asyncpg://$DB_OWNER:$DB_OWNER_PWD@127.0.0.1:$PG_PORT/$DB_NAME"; then
      ok "数据库就绪（便携实例，端口 ${PG_PORT}）"
    else
      pg_provision
      ok "数据库就绪（便携实例，端口 ${PG_PORT}，角色与 RLS 已初始化）"
    fi
    return 0
  fi

  # ③ 本机已有 PG 在监听 → 直接复用
  if port_listening "$PG_PORT"; then
    if pg_ready "postgresql+asyncpg://$DB_OWNER:$DB_OWNER_PWD@127.0.0.1:$PG_PORT/$DB_NAME"; then
      ok "复用本机已有 PostgreSQL（端口 ${PG_PORT}）"
      return 0
    fi
    warn "端口 $PG_PORT 有服务在听，但用 .env 里的账号连不上数据库 $DB_NAME"
    dim "若是你自己装的 PG，请手工建库建角色；或改 .env 里的 DATABASE_URL 指向它"
    dim "也可以让本脚本接管：CROSSPILOT_PG_PORT=5433 ./dev.sh db  （脚本会用另一端口起便携实例）"
    exit 1
  fi

  # ④ Docker 可用 → 交给 compose（容器会自动执行同一份 init.sql）
  if have docker && docker info >/dev/null 2>&1; then
    step "检测到 Docker，用 compose 起数据库依赖"
    ( cd "$ROOT" && docker compose up -d postgres redis ) || die "docker compose 启动失败"
    if wait_port "$PG_PORT" 40; then
      ok "PostgreSQL 容器已就绪（端口 ${PG_PORT}）"
      return 0
    fi
    die "PostgreSQL 容器 40 秒内未就绪，查看：docker compose logs postgres"
  fi

  # ⑤ 什么都没有 → 给出唯一需要动手的一步
  err "本机没有可用的 PostgreSQL，且未安装 Docker"
  echo
  printf '%s请二选一：%s\n' "$C_BOLD" "$C_OFF"
  printf '  %sA. 让本脚本自带一个便携 PostgreSQL（推荐，一次性下载约 30MB）%s\n' "$C_GREEN" "$C_OFF"
  printf '     ./dev.sh db-install\n'
  printf '  %sB. 装 Docker Desktop，然后用容器跑依赖%s\n' "$C_GREEN" "$C_OFF"
  printf '     make up\n'
  echo
  dim "选 A 之后，日常启动就只需要 ./dev.sh 一条命令。"
  exit 1
}

# ---------------------------------------------------------------- 迁移 / 种子
cmd_migrate() {
  detect_python
  step "应用数据库迁移"
  ( cd "$BACKEND" && "$VENV_PY" -m alembic upgrade head ) || die "迁移失败"
  ok "迁移已应用（head）"
}

cmd_seed() {
  detect_python
  step "写入开发种子数据"
  ( cd "$BACKEND" && "$VENV_PY" -m scripts.seed_dev ) || die "种子数据写入失败"
  ok "种子数据就绪"
}

cmd_db() {
  ensure_dirs
  provision_pg
  cmd_migrate
  cmd_seed
  ok "数据库可用"
}

cmd_db_reset() {
  ensure_dirs
  detect_python
  warn "将要**清空**开发库 $DB_NAME 并重跑迁移 —— 仅限本机开发数据"
  printf '  继续请回复 yes：'
  local reply=""
  read -r reply || true
  [ "$reply" = "yes" ] || { dim "已取消"; return 0; }
  provision_pg

  local psql
  psql="$(pg_bin_dir)/psql"
  if [ -x "$psql" ]; then
    step "重建数据库 $DB_NAME"
    "$psql" -h 127.0.0.1 -p "$PG_PORT" -U "$DB_OWNER" -d postgres -q \
      -c "DROP DATABASE IF EXISTS \"$DB_NAME\" WITH (FORCE)" >/dev/null
  else
    # 便携包没有 psql，用 asyncpg 顶
    step "重建数据库 $DB_NAME"
    "$VENV_PY" - "$DB_OWNER" "$DB_OWNER_PWD" "$PG_PORT" "$DB_NAME" <<'PY' || die "重建失败"
import asyncio
import sys

import asyncpg

owner, password, port, dbname = sys.argv[1:5]


async def main() -> None:
    conn = await asyncpg.connect(
        user=owner, password=password, host="127.0.0.1", port=int(port), database="postgres"
    )
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    finally:
        await conn.close()


asyncio.run(main())
PY
  fi
  cmd_db
}

# ---------------------------------------------------------------- 进程
start_api() {
  detect_python
  local pid
  pid="$(pid_of api)"
  if pid_alive "$pid"; then
    ok "API 已在运行（pid ${pid}）"
    return 0
  fi
  if port_listening "$API_PORT"; then
    die "端口 $API_PORT 被占（$(port_owner "$API_PORT")）。先 ./dev.sh stop 或改 CROSSPILOT_API_PORT"
  fi

  step "启动 API（http://$API_HOST:${API_PORT}，热重载）"
  ( cd "$BACKEND" && exec "$VENV_PY" -m uvicorn app.main:app \
      --reload --host "$API_HOST" --port "$API_PORT" ) \
    >"$LOG_DIR/api.log" 2>&1 &
  echo $! >"$RUN_DIR/api.pid"

  if wait_http "http://$API_HOST:$API_PORT/healthz" 40; then
    ok "API 就绪（日志：.dev/logs/api.log）"
  else
    err "API 40 秒内未就绪，日志尾部："
    tail -25 "$LOG_DIR/api.log" >&2 || true
    exit 1
  fi
}

start_web() {
  detect_node
  local pid
  pid="$(pid_of web)"
  if pid_alive "$pid"; then
    ok "前端已在运行（pid ${pid}）"
    return 0
  fi
  if port_listening "$WEB_PORT"; then
    die "端口 $WEB_PORT 被占（$(port_owner "$WEB_PORT")）。先 ./dev.sh stop 或改 CROSSPILOT_WEB_PORT"
  fi
  [ -d "$FRONTEND/node_modules" ] || die "前端依赖未安装。修复：make frontend-install"

  step "启动前端（http://localhost:${WEB_PORT}）"
  ( cd "$FRONTEND" && exec "$NPM_BIN" run dev -- --port "$WEB_PORT" --strictPort ) \
    >"$LOG_DIR/web.log" 2>&1 &
  echo $! >"$RUN_DIR/web.pid"

  if wait_port "$WEB_PORT" 45; then
    ok "前端就绪（日志：.dev/logs/web.log）"
  else
    err "前端 45 秒内未就绪，日志尾部："
    tail -25 "$LOG_DIR/web.log" >&2 || true
    exit 1
  fi
}

stop_one() { # $1=name
  local name="$1" pid
  pid="$(pid_of "$name")"
  if pid_alive "$pid"; then
    kill_tree "$pid" TERM
    local i=0
    while [ "$i" -lt 10 ] && pid_alive "$pid"; do sleep 0.5; i=$((i + 1)); done
    pid_alive "$pid" && kill_tree "$pid" KILL
    ok "已停止 $name"
  fi
  rm -f "$RUN_DIR/$name.pid"
}

cmd_stop() {
  local stopped=0
  for n in web api; do
    if pid_alive "$(pid_of "$n")"; then stop_one "$n"; stopped=1; fi
  done
  # 只在实例确实存在时停 PG —— 别把用户自己装的 PG 停了
  if [ -x "$PG_HOME/bin/pg_ctl" ] && pg_running; then
    step "停止 PostgreSQL"
    pg_stop
    ok "已停止 PostgreSQL"
    stopped=1
  fi
  [ "$stopped" = 0 ] && dim "没有由本脚本启动的进程"
  return 0
}

cmd_status() {
  ensure_dirs
  detect_python 2>/dev/null || true
  printf '%sCrossPilot 运行状态%s\n' "$C_BOLD" "$C_OFF"

  local pid
  for n in api web; do
    pid="$(pid_of "$n")"
    if pid_alive "$pid"; then
      printf '  %-6s %s运行中%s (pid %s)\n' "$n" "$C_GREEN" "$C_OFF" "$pid"
    elif port_listening "$(  [ "$n" = api ] && echo "$API_PORT" || echo "$WEB_PORT")"; then
      printf '  %-6s %s端口被占用但非本脚本启动%s\n' "$n" "$C_YELLOW" "$C_OFF"
    else
      printf '  %-6s %s已停止%s\n' "$n" "$C_DIM" "$C_OFF"
    fi
  done

  if [ -x "$PG_HOME/bin/pg_ctl" ] && pg_running; then
    printf '  %-6s %s运行中%s (便携实例, 端口 %s)\n' "db" "$C_GREEN" "$C_OFF" "$PG_PORT"
  elif port_listening "$PG_PORT"; then
    printf '  %-6s %s运行中%s (外部实例, 端口 %s)\n' "db" "$C_GREEN" "$C_OFF" "$PG_PORT"
  else
    printf '  %-6s %s已停止%s\n' "db" "$C_DIM" "$C_OFF"
  fi

  if pid_alive "$(pid_of api)"; then
    local health
    health="$(curl -fsS --max-time 3 "http://$API_HOST:$API_PORT/healthz" 2>/dev/null || echo '无响应')"
    dim "healthz: $health"
    local ready
    ready="$(curl -fsS --max-time 5 "http://$API_HOST:$API_PORT/readyz" 2>/dev/null || echo '无响应')"
    dim "readyz : $ready"
    printf '  %sAPI 文档 http://%s:%s/docs%s\n' "$C_BLUE" "$API_HOST" "$API_PORT" "$C_OFF"
  fi
  if pid_alive "$(pid_of web)"; then
    printf '  %s前端     http://localhost:%s%s\n' "$C_BLUE" "$WEB_PORT" "$C_OFF"
  fi
}

cmd_logs() {
  ensure_dirs
  case "${1:-}" in
    api) tail -n 100 -f "$LOG_DIR/api.log" ;;
    web) tail -n 100 -f "$LOG_DIR/web.log" ;;
    pg)  tail -n 100 -f "$LOG_DIR/pg.log" ;;
    *)   tail -n 60 -f "$LOG_DIR/api.log" "$LOG_DIR/web.log" ;;
  esac
}

# ---------------------------------------------------------------- up
cmd_up() {
  ensure_dirs
  detect_python
  cmd_env

  printf '\n%s┌──────────────────────────────────────────────┐%s\n' "$C_BOLD" "$C_OFF"
  printf '%s│%s  %sCrossPilot 本机开发环境%s                    %s│%s\n' "$C_BOLD" "$C_OFF" "$C_BLUE$C_BOLD" "$C_OFF" "$C_BOLD" "$C_OFF"
  printf '%s└──────────────────────────────────────────────┘%s\n\n' "$C_BOLD" "$C_OFF"

  # 数据库必须在 API 之前就绪：否则 uvicorn 起来了但每个请求都 500
  cmd_db

  start_api
  start_web

  echo
  printf '%s就绪：%s\n' "$C_GREEN$C_BOLD" "$C_OFF"
  printf '  前端    %shttp://localhost:%s%s\n' "$C_BLUE" "$WEB_PORT" "$C_OFF"
  printf '  API     %shttp://%s:%s%s\n' "$C_BLUE" "$API_HOST" "$API_PORT" "$C_OFF"
  printf '  接口文档 %shttp://%s:%s/docs%s\n' "$C_BLUE" "$API_HOST" "$API_PORT" "$C_OFF"
  local demo_email
  demo_email="$(grep -m1 '^DEMO_OWNER_EMAIL' "$BACKEND/scripts/seed_dev.py" 2>/dev/null | sed -E 's/.*"([^"]+)".*/\1/' || true)"
  [ -n "$demo_email" ] || demo_email="owner@example.com"
  printf '  演示账号 %s（密码见 backend/scripts/seed_dev.py）\n' "$demo_email"
  echo
  dim "日志：./dev.sh logs       停止：Ctrl+C 或 ./dev.sh stop"
  echo

  # Ctrl+C / 终止信号 → 收掉本次起的全部进程，不留孤儿占端口
  trap 'echo; step "收到退出信号，正在停止…"; cmd_stop; exit 0' INT TERM
  tail -n 20 -f "$LOG_DIR/api.log" "$LOG_DIR/web.log" &
  local tail_pid=$!
  wait "$tail_pid" 2>/dev/null || true
  # tail 被 Ctrl+C 打断后仍走一次清理（trap 已触发则这里是幂等的）
  cmd_stop
  echo
  ok "已全部停止"
}

cmd_help() {
  cat <<'EOF'
CrossPilot 本机直跑脚本

用法： ./dev.sh [命令]

不带命令             等价于 up：DB → 迁移 → 种子 → API + 前端
  up                 同上
  api                只起后端（不动前端）
  web                只起前端
  stop               停止本脚本启动的全部进程（含便携 PG）
  restart            stop 之后重新 up
  status             查看进程 / 端口 / 健康探针状态
  logs [api|web|pg]  跟踪日志（不带参数则同时跟 api+web 两个）

  doctor             体检：依赖、.env、数据库、迁移版本，只读不改
  env                由 .env.example 生成 .env（随机密钥，已存在则不动）

  db                 只处理数据库：供给 → 迁移 → 种子
  db-install         下载安装便携 PostgreSQL（约 30MB，免 Docker / brew）
  db-reset           重建开发库（需输入 yes 确认）
  migrate            只跑 alembic upgrade head
  seed               只写种子数据
  sql "SELECT 1"     直接查库（便携包不带 psql，用这个顶上；不带参数则读 stdin）

  test               后端全量测试（含覆盖率门禁）
  test-unit          后端单元测试（不需要数据库）
  lint               静态检查（ruff + eslint）
  typecheck          类型检查（mypy + tsc）

  help               本帮助

环境变量覆盖（一般用不到）：
  CROSSPILOT_PG_PORT        便携 PG 端口，默认 5432
  CROSSPILOT_API_PORT       API 端口，默认 8000
  CROSSPILOT_WEB_PORT       前端端口，默认 5173
  CROSSPILOT_PG_MIRROR      PG 二进制下载源
  CROSSPILOT_DATABASE_URL   指向已有数据库时跳过自动供给
  NO_COLOR=1                关闭彩色输出
EOF
}

cmd_test() {
  detect_python
  ( cd "$BACKEND" && "$VENV_PY" -m pytest --cov=app --cov-report=term-missing --cov-fail-under=70 )
}
cmd_test_unit() {
  detect_python
  ( cd "$BACKEND" && "$VENV_PY" -m pytest tests/unit -q )
}
cmd_lint() {
  detect_python
  ( cd "$BACKEND" && .venv/bin/ruff check --fix app tests alembic scripts \
      && .venv/bin/ruff format app tests alembic scripts )
  # 根级 CLI 脚本走自己的 ruff.toml（CLI 工具必须 print）
  "$BACKEND/.venv/bin/ruff" check --fix scripts && "$BACKEND/.venv/bin/ruff" format scripts
  detect_node && ( cd "$FRONTEND" && "$NPM_BIN" run lint --if-present )
}
cmd_typecheck() {
  detect_python
  ( cd "$BACKEND" && .venv/bin/mypy app )
  detect_node && ( cd "$FRONTEND" && "$NPM_BIN" run typecheck )
}

# ---------------------------------------------------------------- 体检
cmd_doctor() {
  ensure_dirs
  detect_python 2>/dev/null || true
  printf '%sCrossPilot 环境体检%s（只读，不改动任何文件）\n\n' "$C_BOLD" "$C_OFF"

  # --- 工具链
  printf '%s工具链%s\n' "$C_BOLD" "$C_OFF"
  if [ -n "$VENV_PY" ] && [ -x "$VENV_PY" ]; then
    printf '  %s✓%s 后端 venv      %s（%s）\n' "$C_GREEN" "$C_OFF" "$VENV_PY" "$("$VENV_PY" -V 2>&1)"
    for m in fastapi uvicorn sqlalchemy alembic; do
      if "$VENV_PY" -c "import $m" >/dev/null 2>&1; then
        printf '    %s✓%s %s\n' "$C_GREEN" "$C_OFF" "$m"
      else
        printf '    %s✗%s %s %s缺失%s\n' "$C_RED" "$C_OFF" "$m" "$C_DIM" "$C_OFF"
      fi
    done
  else
    printf '  %s✗%s 后端 venv 缺失 —— %smake backend-install%s\n' "$C_RED" "$C_OFF" "$C_DIM" "$C_OFF"
  fi
  if detect_node 2>/dev/null; then
    printf '  %s✓%s npm           %s\n' "$C_GREEN" "$C_OFF" "$NPM_BIN"
  else
    printf '  %s✗%s npm 缺失\n' "$C_RED" "$C_OFF"
  fi
  if [ -d "$FRONTEND/node_modules" ] && [ -x "$FRONTEND/node_modules/.bin/vite" ]; then
    printf '  %s✓%s 前端依赖      node_modules 就绪\n' "$C_GREEN" "$C_OFF"
  else
    printf '  %s✗%s 前端依赖      %smake frontend-install%s\n' "$C_RED" "$C_OFF" "$C_DIM" "$C_OFF"
  fi

  # --- 配置
  printf '\n%s配置%s\n' "$C_BOLD" "$C_OFF"
  if [ -f "$ENV_FILE" ]; then
    printf '  %s✓%s .env 存在\n' "$C_GREEN" "$C_OFF"
    local jwt aesv
    jwt="$(env_get JWT_SECRET)"
    case "$jwt" in
      ""|change-me-to-a-64-char-random-string) printf '    %s!%s JWT_SECRET 仍是默认值 —— %s./dev.sh env 可重建%s\n' "$C_YELLOW" "$C_OFF" "$C_DIM" "$C_OFF" ;;
      *) printf '    %s✓%s JWT_SECRET 已设置（%s 字符）\n' "$C_GREEN" "$C_OFF" "${#jwt}" ;;
    esac
    aesv="$(env_get CREDENTIAL_AES_KEY)"
    [ -n "$aesv" ] && printf '    %s✓%s CREDENTIAL_AES_KEY 已设置\n' "$C_GREEN" "$C_OFF" \
                   || printf '    %s!%s CREDENTIAL_AES_KEY 为空 —— %s./dev.sh env 可重建%s\n' "$C_YELLOW" "$C_OFF" "$C_DIM" "$C_OFF"
  else
    printf '  %s!%s .env 不存在 —— %s./dev.sh env%s\n' "$C_YELLOW" "$C_OFF" "$C_DIM" "$C_OFF"
  fi

  # --- 数据库
  printf '\n%s数据库%s\n' "$C_BOLD" "$C_OFF"
  if [ -x "$PG_HOME/bin/pg_ctl" ]; then
    printf '  %s✓%s 便携 PostgreSQL  %s\n' "$C_GREEN" "$C_OFF" "$PG_HOME"
    if pg_running; then
      printf '    %s✓%s 实例运行中（端口 %s）\n' "$C_GREEN" "$C_OFF" "$PG_PORT"
    else
      printf '    %s·%s 实例未启动 —— %s./dev.sh db%s\n' "$C_DIM" "$C_OFF" "$C_DIM" "$C_OFF"
    fi
  elif find_pg_ctl >/dev/null 2>&1; then
    printf '  %s✓%s 系统 PostgreSQL  %s\n' "$C_GREEN" "$C_OFF" "$(find_pg_ctl)"
  elif port_listening "$PG_PORT"; then
    printf '  %s✓%s 端口 %s 有 PostgreSQL（外部实例）\n' "$C_GREEN" "$C_OFF" "$PG_PORT"
  elif have docker && docker info >/dev/null 2>&1; then
    printf '  %s·%s 未找到本机 PG，但 Docker 可用（%smake up%s）\n' "$C_DIM" "$C_OFF" "$C_DIM" "$C_OFF"
  else
    printf '  %s✗%s 无可用 PostgreSQL —— %s./dev.sh db-install%s（约 30MB，一次性）\n' "$C_YELLOW" "$C_OFF" "$C_DIM" "$C_OFF"
  fi

  # --- 端口
  printf '\n%s端口%s\n' "$C_BOLD" "$C_OFF"
  local p
  for p in "$API_PORT:API" "$WEB_PORT:前端" "$PG_PORT:PostgreSQL"; do
    local port="${p%%:*}" label="${p##*:}"
    if port_listening "$port"; then
      printf '  %s!%s %-5s %-12s 被占用（%s）\n' "$C_YELLOW" "$C_OFF" "$port" "$label" "$(port_owner "$port")"
    else
      printf '  %s✓%s %-5s %-12s 空闲\n' "$C_GREEN" "$C_OFF" "$port" "$label"
    fi
  done
  echo
}

# ---------------------------------------------------------------- 分发
main() {
  local cmd="${1:-up}"
  shift 2>/dev/null || true
  case "$cmd" in
    up|"")        cmd_up ;;
    api)          ensure_dirs; detect_python; cmd_env; cmd_db; start_api; sleep 1; trap 'echo; cmd_stop; exit 0' INT TERM; tail -n 20 -f "$LOG_DIR/api.log" ;;
    web)          ensure_dirs; detect_node; start_web; sleep 1; trap 'echo; cmd_stop; exit 0' INT TERM; tail -n 20 -f "$LOG_DIR/web.log" ;;
    stop)         cmd_stop ;;
    restart)      cmd_stop; cmd_up ;;
    status)       cmd_status ;;
    logs)         cmd_logs "${1:-}" ;;
    doctor)       cmd_doctor ;;
    env)          cmd_env ;;
    db)           cmd_db ;;
    db-install)   cmd_env; cmd_db_install ;;
    db-reset)     cmd_db_reset ;;
    migrate)      cmd_migrate ;;
    seed)         cmd_seed ;;
    sql)          pg_sql "$@" ;;
    test)         cmd_test ;;
    test-unit)    cmd_test_unit ;;
    lint)         cmd_lint ;;
    typecheck)    cmd_typecheck ;;
    help|-h|--help) cmd_help ;;
    *)            err "未知命令：$cmd"; echo; cmd_help; exit 1 ;;
  esac
}

main "$@"
