#!/usr/bin/env python3
"""拦截 shell 脚本里的一类隐蔽 bug：`$VAR` 后紧跟非 ASCII 字符会被吞字节。

问题背景
--------
bash 3.2（macOS 自带）在**多字节 locale**（如 `en_US.UTF-8`）下解析 `$VAR` 时，
会把紧随其后的多字节字符首字节并入变量名：

    set -u; PG_PORT=5432
    echo "端口 $PG_PORT）"      # → PG_PORT<乱码>: unbound variable

同一段代码在 `LC_CTYPE=C` 下**完全正常**。于是只在「中文输出 + 用户终端是 UTF-8」
的组合上炸，而且 `bash -n` 语法检查、`shellcheck`（未装时）都看不出来。

修法：一律写成 `${VAR}）`，让变量名有明确边界。

用法
----
    python3 scripts/check_shell_locale.py dev.sh scripts/*.sh
    python3 scripts/check_shell_locale.py --all      # 扫全仓库的 *.sh

退出码：0 = 干净；1 = 发现问题。
零依赖（不 import 任何第三方包），可直接用作 pre-commit hook。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# `$VAR` / `$1` / `$?` 等，且后面紧跟非 ASCII（说明变量名会被多字节字节污染）
BAD = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*|[0-9]|[!#?@*$-])(?=[^\x00-\x7F])")

# heredoc：<<'PY' / <<"PY" / <<PY / <<-EOF
HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")

SKIP_DIRS = {".git", "node_modules", ".venv", ".dev", ".devtools", "__pycache__", "dist", "build"}


def scan(path: Path) -> list[tuple[int, str, str]]:
    """返回 [(行号, 变量名, 该行原文), ...]。"""
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except (UnicodeDecodeError, OSError):
        return []

    hits: list[tuple[int, str, str]] = []
    pending: tuple[str, bool] | None = None  # (delimiter, 是否不展开)

    for i, line in enumerate(lines, 1):
        if pending:
            delim, quoted = pending
            if line.strip() == delim:
                pending = None
            elif not quoted:  # 会展开的 heredoc 也要查
                for m in BAD.finditer(line):
                    hits.append((i, m.group(1), line.strip()))
            continue

        for m in HEREDOC.finditer(line):
            pending = (m.group(3), m.group(2) != "")
            break

        for m in BAD.finditer(line):
            hits.append((i, m.group(1), line.strip()))

    return hits


def collect(argv: list[str]) -> list[Path]:
    if "--all" in argv:
        root = Path(".").resolve()
        return sorted(p for p in root.rglob("*.sh") if not any(part in SKIP_DIRS for part in p.parts))
    return [Path(a) for a in argv if not a.startswith("-")]


def main(argv: list[str]) -> int:
    files = collect(argv)
    if not files:
        print("check_shell_locale: 未提供文件，跳过")
        return 0

    total = 0
    for path in files:
        for lineno, var, text in scan(path):
            total += 1
            print(f"{path}:{lineno}: ${{{var}}} —— 当前写作 `${var}` 且后紧跟非 ASCII 字符")
            print(f"    {text[:110]}")

    if total:
        print()
        print(f"✗ check_shell_locale: 发现 {total} 处。")
        print("  原因：bash 3.2 在多字节 locale 下会把后一个字符的首字节并入变量名。")
        print("  修法：把 `$VAR` 改成 `${VAR}`（变量名加花括号定界）。")
        return 1

    print(f"✓ check_shell_locale: {len(files)} 个 shell 文件通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
