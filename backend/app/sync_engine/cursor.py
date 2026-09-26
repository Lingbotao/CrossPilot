"""订单增量游标（M2-05）。

拉取窗口是「上次成功的高水位 − 重叠时长」到现在。重叠多取到的订单靠幂等键去重。
分页在页数上限处停下，并把平台返回的下一页游标记下来，下一轮从那里继续，而不是把时间水位向前拨。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.adapters.base import PageResult, UnifiedOrder

PageFetch = Callable[[str | None], Awaitable[PageResult[UnifiedOrder]]]


@dataclass(frozen=True, slots=True)
class OrderSyncWindow:
    since: datetime
    until: datetime
    page_cursor: str | None
    advance: bool


@dataclass(frozen=True, slots=True)
class PullResult:
    orders: list[UnifiedOrder]
    complete: bool
    resume_cursor: str | None
    pages: int
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class CursorCheckpoint:
    """``cursor_at is None`` 表示保留已有高水位，只更新断点续传字段。"""

    cursor_at: datetime | None
    resume_cursor: str | None
    resume_since: datetime | None
    resume_until: datetime | None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def order_sync_window(
    *,
    cursor_at: datetime | None,
    now: datetime,
    overlap: timedelta,
    initial_lookback: timedelta,
) -> tuple[datetime, datetime]:
    """没有高水位时回看 ``initial_lookback``；否则从高水位再向前重叠一段。"""
    until = _as_utc(now)
    since = until - initial_lookback if cursor_at is None else _as_utc(cursor_at) - overlap
    if since >= until:
        since = until - (overlap if overlap > timedelta(0) else timedelta(seconds=1))
    return since, until


def is_order_sync_due(
    *,
    cursor_at: datetime | None,
    resume_cursor: str | None,
    now: datetime,
    interval: timedelta,
) -> bool:
    """有未完成的分页，或距离上次高水位已经超过同步间隔。"""
    if resume_cursor:
        return True
    if cursor_at is None:
        return True
    return _as_utc(cursor_at) + interval <= _as_utc(now)


def shop_sync_message(*, shop_id: int, tenant_id: int, platform: str, trigger: str) -> dict[str, object]:
    """入队参数。``platform`` 必须在 kwargs 里，任务才会进对应平台的队列。"""
    return {
        "shop_id": shop_id,
        "tenant_id": tenant_id,
        "platform": platform,
        "trigger": trigger,
    }


def checkpoint_for(
    *,
    complete: bool,
    errored: bool,
    failed: int,
    until: datetime,
    resume_cursor: str | None,
    since: datetime,
) -> CursorCheckpoint | None:
    """返回 None 表示这次不要动游标：失败、或分页卡住又没有可续传的令牌。"""
    if errored or failed:
        return None
    if complete:
        return CursorCheckpoint(
            cursor_at=_as_utc(until),
            resume_cursor=None,
            resume_since=None,
            resume_until=None,
        )
    if resume_cursor:
        return CursorCheckpoint(
            cursor_at=None,
            resume_cursor=resume_cursor,
            resume_since=_as_utc(since),
            resume_until=_as_utc(until),
        )
    return None


async def pull_order_pages(
    fetch: PageFetch,
    *,
    start_cursor: str | None,
    max_pages: int,
) -> PullResult:
    """按页拉取，直到没有下一页、游标重复，或达到页数上限。"""
    if max_pages < 1:
        raise ValueError("max_pages 至少为 1")
    collected: list[UnifiedOrder] = []
    seen: set[str] = set()
    cursor = start_cursor
    if cursor:
        seen.add(cursor)
    pages = 0
    while pages < max_pages:
        try:
            page = await fetch(cursor)
        except Exception as exc:
            return PullResult(
                orders=collected,
                complete=False,
                resume_cursor=None,
                pages=pages,
                error=exc,
            )
        collected.extend(page.items)
        pages += 1
        nxt = page.next_cursor
        if not nxt:
            return PullResult(orders=collected, complete=True, resume_cursor=None, pages=pages)
        if nxt in seen:
            return PullResult(orders=collected, complete=False, resume_cursor=None, pages=pages)
        seen.add(nxt)
        cursor = nxt
    return PullResult(orders=collected, complete=False, resume_cursor=cursor, pages=pages)


__all__ = [
    "CursorCheckpoint",
    "OrderSyncWindow",
    "PullResult",
    "checkpoint_for",
    "is_order_sync_due",
    "order_sync_window",
    "pull_order_pages",
    "shop_sync_message",
]
