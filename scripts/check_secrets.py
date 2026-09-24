#!/usr/bin/env python3
"""密钥扫描（pre-commit hook + CI 门禁）。

## 为什么自己写一个

`gitleaks` / `detect-secrets` 功能更强，但都需要额外安装二进制或维护基线文件；
本项目要覆盖的场景其实很集中：**平台凭证、JWT 密钥、AES 密钥、对象存储密钥**。
一个零依赖脚本能让"新同学 clone 下来就能跑 pre-commit"，比功能强大更重要。

## 扫描策略

1. **已知格式强匹配**：AWS Access Key、各类私钥头、JWT、企业微信/钉钉 webhook、
   各平台 token 前缀 —— 这些有固定形态，命中即报。
2. **赋值式启发**：`SECRET = "..."`、`password: '...'` 这类写法，
   再叠加**占位符白名单**（`change-me`、`your-`、`xxx`、`<...>`）与**熵值判断**。
3. **高熵长串**：长度 ≥32 且熵值高的 base64/hex 串 —— 常见于误提交的随机密钥。

## 误报处理

在文件任意位置加一行：

    secret-scan: allow

或在具体行的行尾加：

    API_KEY = "..."  # secret-scan: allow

## 用法

    python3 scripts/check_secrets.py                 # 扫描全部受版本控制的文本文件
    python3 scripts/check_secrets.py file1 file2     # 只扫描指定文件（pre-commit 传入）
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- 配置

ALLOW_MARKER = "secret-scan: allow"

SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".svg",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".lock",
    ".mp4",
    ".mov",
    ".xlsx",
    ".docx",
    ".pptx",
    ".pyc",
}

SKIP_PATH_PARTS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "site-packages",
    ".next",
    "coverage",
    "htmlcov",
}

# 这些文件本身就是"放占位符"的地方，但仍然会被扫描（只是允许更多占位形态）
EXAMPLE_FILE_HINTS = (".env.example", ".env.sample", ".env.template")

# ---- 形态确定的密钥：命中即高危 ----
PATTERN_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("AWS Access Key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "高危"),
    (
        "私钥文件内容",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
        "高危",
    ),
    ("JWT 令牌", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "中危"),
    (
        "企业微信 Webhook",
        re.compile(r"https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[A-Za-z0-9-]{16,}"),
        "高危",
    ),
    ("钉钉 Webhook", re.compile(r"https://oapi\.dingtalk\.com/robot/send\?access_token=[A-Za-z0-9]{20,}"), "高危"),
    ("Slack Token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "高危"),
    ("Google API Key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "高危"),
    ("Stripe Live Key", re.compile(r"\bsk_live_[0-9A-Za-z]{24,}\b"), "高危"),
    ("Shopee/Lazada 形似长密钥", re.compile(r"\b(?:partner_key|app_secret)\s*[:=]\s*['\"][0-9a-f]{32,}['\"]"), "中危"),
]

# ---- 赋值式：需要结合占位符白名单与熵值判断 ----
SENSITIVE_KEY_PATTERN = re.compile(
    r"""(?P<key>\b(?:password|passwd|pwd|secret|token|api_?key|access_?key|secret_?key|"""
    r"""private_?key|credential|auth_?key|app_?secret|client_?secret|webhook|salt)\b)""",
    re.IGNORECASE,
)
ASSIGN_PATTERN = re.compile(
    r"""(?P<key_quote>['"]?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?P=key_quote)\s*[:=]\s*"""
    r"""(?P<value_quote>['"])(?P<value>[^'"]{8,})(?P=value_quote)"""
)

PLACEHOLDER_PATTERNS = [
    re.compile(r"^(?:change[-_]?me|replace[-_]?me|your[-_]|my[-_]|example|demo|test|dummy|fake)", re.IGNORECASE),
    re.compile(r"^(?:x{3,}|\*{3,}|-{3,}|_{3,}|\.{3,})$"),
    re.compile(r"<[^>]+>"),  # <YOUR_KEY>
    re.compile(r"^\$\{?[A-Z_]+\}?$"),  # ${ENV_VAR}
    re.compile(r"^\{\{[^}]+\}\}$"),  # {{ template }}
    re.compile(r"^(?:todo|todo_|please_)", re.IGNORECASE),
    re.compile(r"not[-_]?a[-_]?real", re.IGNORECASE),
]

# 短于这个长度的值不做熵值判断（误报太多）
MIN_ENTROPY_LENGTH = 32

# ---- 自检样本：(文本, 期望是否命中) ----
# 高熵启发式是统计规则，改动它很容易「把误报压下去、顺手把检出能力也改坏」，
# 而 CI 只会因为误报变红、对漏报毫无察觉。这组样本把两侧边界钉住。
SELF_TEST_CASES: list[tuple[str, bool]] = [
    # —— 必须抓到 ——
    ("AKIAIOSFODNN7EXAMPLE", True),  # AWS Access Key
    ("-----BEGIN RSA PRIVATE KEY-----", True),  # 私钥头
    ('api_key = "aB3xK9mP2qR7sT4vW8yZ5cD1eF6gH0jL"', True),  # 赋值式 + 高熵
    ('password = "correcthorsebatterystaple"', True),  # 赋值式：无数字也要拦
    ("kQ3vN8pLx2RzM7tYwB4cD9fGhJ5sV1nE", True),  # 裸高熵串（无 key 名）
    (
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=12345678-1234-1234-1234-1234567890ab",
        True,
    ),  # 企业微信 Webhook
    # —— 不得误报 ——
    ("refresh_expiring_credentials_tick", False),  # 日志事件名（蛇形）
    ("naming_convention=NAMING_CONVENTION", False),  # 配置表达式
    ("CredentialCipherNotConfiguredError", False),  # CamelCase 类名
    ("0123456789ABCDEFGHJKMNPQRSTVWXYZ", False),  # Crockford Base32 字符表
    ("backend/tests/security/test_tenant_isolation.py", False),  # 文件路径
    ("foreign_pre_chain=_shared_processors", False),  # 关键字参数
]


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def looks_like_path(value: str) -> bool:
    """文件路径不是密钥。

    Markdown 表格里的 `backend/tests/security/test_tenant_isolation.py` 这类长路径
    会踩中高熵启发式（长度 >32、字符集单一、含斜杠）。判据取「全小写 + 含斜杠」：
    真实随机密钥在 32 字符上几乎不可能全是小写 —— 标准 base64 必然含大写字母，
    而 base64url（`secrets.token_urlsafe` 用的）不含 `/`。所以这个过滤不会放过密钥。
    """
    if "/" not in value:
        return False
    return bool(re.fullmatch(r"[a-z0-9_./-]+", value))


def looks_like_placeholder(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return True
    if any(pattern.search(stripped) for pattern in PLACEHOLDER_PATTERNS):
        return True
    # 全是同一个字符、或者是明显的自然语言句子，都不算密钥
    if len(set(stripped)) <= 3:
        return True
    # 含中文说明的（如 "请替换为真实密钥"）是文档，不是密钥
    if re.search(r"[\u4e00-\u9fff]", stripped):
        return True
    return looks_like_path(stripped)


def has_monotonic_run(value: str, run: int = 8) -> bool:
    """是否含 ≥run 个连续递增/递减的字符（如 `0123456789`、`ABCDEFGH`）。

    字符表（Crockford Base32 字母表之类）会有这种长链，随机密钥不会有。
    """
    asc = desc = 1
    # 用索引而不是 zip(pairwise)：本脚本要求兼容 macOS 自带的 Python 3.9，
    # 而 itertools.pairwise / zip(strict=) 都要 3.10+。
    for index in range(1, len(value)):
        prev, cur = value[index - 1], value[index]
        if ord(cur) == ord(prev) + 1:
            asc += 1
            desc = 1
        elif ord(cur) == ord(prev) - 1:
            desc += 1
            asc = 1
        else:
            asc = desc = 1
        if asc >= run or desc >= run:
            return True
    return False


def looks_like_identifier(value: str) -> bool:
    """标识符 / 配置表达式 / 字符表 —— 不是密钥。

    高熵启发式是纯统计的，对**源码里的标识符**误报极高（实测 102 个源文件命中 22 处，
    全部是 `refresh_expiring_credentials_tick`、`naming_convention=NAMING_CONVENTION`、
    `CredentialCipherNotConfiguredError` 这类东西）。它们的形态与随机密钥有稳定差异：

    1. **配置表达式**：`=` 出现在中间 —— base64 的 `=` 只可能是**末尾**补位。
    2. **分段式命名**：≥3 段、每段都是纯小写字母或纯数字（蛇形/串形名）。
    3. **一个数字都没有**：自然语言词与 CamelCase 标识符；随机密钥含数字的概率极高。
    4. **含 8 个以上连续递增/递减字符**：字符表、序列，不是随机串。

    本函数**只用于高熵启发式**，不用于赋值式规则 —— 后者由密钥名（`password` / `secret`
    等）驱动，本身就是语义判断，不需要形态过滤（否则会漏掉
    `password = "阅读型口令短语"` 这类真实泄露）。
    """
    if "=" in value[:-2]:
        return True
    parts = re.split(r"[_\-.]", value)
    if len(parts) >= 3 and all(part == "" or part.islower() or part.isdigit() for part in parts):
        return True
    if not any(char.isdigit() for char in value):
        return True
    return has_monotonic_run(value)


def is_high_entropy(value: str) -> bool:
    if len(value) < MIN_ENTROPY_LENGTH:
        return False
    # 纯十六进制/纯 base64 字符集，且熵足够高
    if not re.fullmatch(r"[A-Za-z0-9+/=_\-]+", value):
        return False
    return shannon_entropy(value) >= 3.6


def iter_target_files(explicit: list[str]) -> list[Path]:
    if explicit:
        return [Path(item) for item in explicit if Path(item).is_file()]

    try:
        output = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [p for p in Path(".").rglob("*") if p.is_file()]

    return [Path(line) for line in output.splitlines() if line.strip()]


def should_skip(path: Path) -> bool:
    if any(part in SKIP_PATH_PARTS for part in path.parts):
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    try:
        return path.stat().st_size > 2 * 1024 * 1024  # 跳过 >2MB 的文件
    except OSError:
        return True


class Finding:
    __slots__ = ("line_no", "path", "rule", "severity", "snippet")

    def __init__(self, path: str, line_no: int, rule: str, severity: str, snippet: str) -> None:
        self.path = path
        self.line_no = line_no
        self.rule = rule
        self.severity = severity
        self.snippet = snippet

    def render(self) -> str:
        return f"  [{self.severity}] {self.path}:{self.line_no}  {self.rule}\n      {self.snippet}"


def redact(value: str, keep: int = 4) -> str:
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 6}{value[-keep:]}"


def scan_file(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return findings

    if ALLOW_MARKER in content:
        return findings

    is_example = any(hint in str(path) for hint in EXAMPLE_FILE_HINTS)

    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        # 注释行跳过 —— 但私钥头之类的强特征即使在注释里也要拦
        if (not line or line.startswith(("#", "//", "/*", "*", "<!--"))) and ("-----BEGIN" not in line):
            continue
        if ALLOW_MARKER in raw_line:
            continue

        # ---- 形态确定的规则 ----
        for rule_name, pattern, severity in PATTERN_RULES:
            match = pattern.search(raw_line)
            if match:
                findings.append(Finding(str(path), line_no, rule_name, severity, redact(match.group(0), 6)))

        # ---- 赋值式 ----
        for match in ASSIGN_PATTERN.finditer(raw_line):
            key = match.group("key")
            value = match.group("value")
            if not SENSITIVE_KEY_PATTERN.search(key):
                continue
            if looks_like_placeholder(value):
                continue
            # .env.example 里的值本来就允许是"看起来像密钥"的样例，但高熵仍需拦
            if not (is_example and not is_high_entropy(value)):
                findings.append(Finding(str(path), line_no, f"疑似硬编码密钥（{key}）", "中危", redact(value)))

        # ---- 高熵长串 ----
        for candidate in re.findall(r"[A-Za-z0-9+/=_\-]{32,}", raw_line):
            if looks_like_placeholder(candidate) or looks_like_identifier(candidate):
                continue
            if is_high_entropy(candidate):
                findings.append(Finding(str(path), line_no, "高熵字符串（疑似密钥）", "中危", redact(candidate)))

    return findings


def run_self_test() -> int:
    """跑内置自检：既确认「真密钥被抓」，也确认「已知误报不被抓」。"""
    import tempfile

    failures: list[tuple[str, bool, bool]] = []
    for sample, should_hit in SELF_TEST_CASES:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as handle:
            handle.write(sample + "\n")
            tmp = Path(handle.name)
        try:
            hit = bool(scan_file(tmp))
        finally:
            tmp.unlink(missing_ok=True)
        status = "✓" if hit == should_hit else "✗"
        print(f"  {status} {'命中' if hit else '未命中'}（期望 {'命中' if should_hit else '未命中'}）  {sample[:52]}")
        if hit != should_hit:
            failures.append((sample, should_hit, hit))

    if failures:
        print(f"\n❌ 自检失败 {len(failures)} / {len(SELF_TEST_CASES)}：规则改动破坏了检出边界")
        return 1
    print(f"\n✅ 自检通过（{len(SELF_TEST_CASES)} 个样本，检出与误报两侧均符合预期）")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return run_self_test()
    files = [path for path in iter_target_files(argv) if not should_skip(path)]

    findings: list[Finding] = []
    for path in files:
        findings.extend(scan_file(path))

    if not findings:
        print(f"✅ 密钥扫描通过（已检查 {len(files)} 个文件）")
        return 0

    print(f"❌ 密钥扫描发现 {len(findings)} 处可疑内容：\n")
    for finding in findings:
        print(finding.render())
    print(
        "\n处理方式：\n"
        "  1) 若是真实密钥 —— 立刻从代码中移除，改走环境变量，并**轮换该密钥**"
        "（已提交过的密钥必须视为已泄露）；\n"
        "  2) 若是误报 —— 在文件内加一行 `secret-scan: allow`，或在行尾加 `# secret-scan: allow`。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
