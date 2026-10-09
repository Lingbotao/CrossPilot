"""文案状态、术语替换和敏感词扫描。不访问数据库，也不调用翻译接口。"""

from __future__ import annotations

import re

from app.core.errors import AppError, ErrorCode
from app.models.locale import (
    QUALITY_MT_DRAFT,
    QUALITY_PUBLISHED,
    QUALITY_REVIEWED,
    QUALITY_UNTRANSLATED,
)


def text_empty(title: str, description: str, bullets: list[str]) -> bool:
    if title.strip() or description.strip():
        return False
    return all(not item.strip() for item in bullets)


def human_status(*, previous: str | None, empty: bool, confirm_review: bool) -> str:
    """人工保存不会把机翻草稿直接标成已发布。未确认校对时，草稿角标保持不变。"""

    if empty:
        return QUALITY_UNTRANSLATED
    if previous == QUALITY_MT_DRAFT and not confirm_review:
        return QUALITY_MT_DRAFT
    return QUALITY_REVIEWED


def assert_publishable(status: str, title: str) -> None:
    if status != QUALITY_REVIEWED:
        raise AppError(
            "未人工校对的语言不能发布",
            code=ErrorCode.CONTENT_NOT_REVIEWED,
            data={"quality_status": status},
        )
    if not title.strip():
        raise AppError("发布前需要填写标题", code=ErrorCode.CONTENT_NOT_REVIEWED, data={"quality_status": status})


def require_published_title(status: str, title: str, *, lang: str) -> str:
    """店铺刊登只接受已经发布的语言文案。"""

    cleaned = title.strip()
    if status != QUALITY_PUBLISHED or not cleaned:
        raise AppError(
            f"该站点语言（{lang}）尚未人工校对并发布",
            code=ErrorCode.CONTENT_NOT_REVIEWED,
            data={"lang": lang, "quality_status": status},
        )
    return cleaned


def apply_glossary(text: str, pairs: list[tuple[str, str]]) -> tuple[str, list[tuple[str, str]]]:
    """较长的术语先替换。大小写不敏感，替换结果用术语库里的目标写法。"""

    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source, target in pairs:
        key = source.casefold()
        if not source.strip() or key in seen:
            continue
        seen.add(key)
        unique.append((source, target))
    ordered = sorted(unique, key=lambda item: len(item[0]), reverse=True)
    if not ordered or not text:
        return text, []
    pattern = re.compile("|".join(re.escape(source) for source, _target in ordered), re.IGNORECASE)
    found: list[tuple[str, str]] = []
    found_keys: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        for source, target in ordered:
            if source.casefold() == raw.casefold():
                key = source.casefold()
                if key not in found_keys:
                    found_keys.add(key)
                    found.append((source, target))
                return target
        return raw

    return pattern.sub(replace, text), found


def scan_sensitive(
    fields: list[tuple[str, str]],
    terms: list[tuple[str, str | None]],
) -> list[tuple[str, str, str | None]]:
    """每个字段、每个词只报一次。不修改原文。"""

    hits: list[tuple[str, str, str | None]] = []
    for field, text in fields:
        folded = text.casefold()
        if not folded:
            continue
        for keyword, suggestion in terms:
            needle = keyword.casefold()
            if needle and needle in folded:
                hits.append((field, keyword, suggestion))
    return hits
