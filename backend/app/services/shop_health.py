"""店铺健康度。纯函数，供列表接口和单测共用。"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models.enums import ShopStatus, SyncStatus


def evaluate_shop_health(
    *,
    status: int,
    refresh_fail_count: int,
    expires_at: datetime | None,
    last_sync_at: datetime | None,
    last_sync_status: int | None,
    last_error: str | None,
    now: datetime,
    lead: timedelta,
    stale: timedelta,
    alert_threshold: int,
) -> tuple[str, str | None]:
    """返回 (green|yellow|red, 原因)。红灯优先于黄灯。"""
    if status == int(ShopStatus.AUTH_EXPIRED) or refresh_fail_count >= alert_threshold:
        return "red", last_error or "授权已失效，请重新授权"
    if last_sync_status == int(SyncStatus.FAILED):
        return "red", last_error or "最近一次同步失败"
    if last_sync_status == int(SyncStatus.PARTIAL):
        return "yellow", "最近一次同步未完成，数据可能不是最新"
    if expires_at is not None and expires_at <= now + lead:
        return "yellow", "令牌即将过期"
    if last_sync_at is None:
        return "yellow", "尚未同步"
    if last_sync_at <= now - stale:
        return "yellow", "同步数据已陈旧"
    return "green", None


def freshness_line(
    *,
    last_sync_status: int | None,
    last_sync_at: datetime | None,
    now: datetime,
    stale: timedelta,
) -> str | None:
    """同步失败、部分成功或超过陈旧阈值时，给出必须展示的提示。正常同步返回 None。"""

    when = "未知时间" if last_sync_at is None else last_sync_at.strftime("%Y-%m-%d %H:%M UTC")
    if last_sync_status == int(SyncStatus.FAILED):
        return f"同步失败，数据更新于 {when}，可能不是最新"
    if last_sync_status == int(SyncStatus.PARTIAL):
        return f"同步未完成，数据更新于 {when}，可能不是最新"
    if last_sync_at is None:
        return "尚未同步，列表可能不是最新"
    if last_sync_at <= now - stale:
        return f"数据更新于 {when}，可能不是最新"
    return None


def resolve_sync_status(*, failed: int, landed: int, errored: bool) -> SyncStatus:
    """有成功写入又有失败时记为部分成功，避免整单标红把已入库的订单藏起来。"""

    if not errored and failed == 0:
        return SyncStatus.SUCCESS
    if landed > 0:
        return SyncStatus.PARTIAL
    return SyncStatus.FAILED


def next_refresh_failure(count: int, threshold: int) -> tuple[int, bool]:
    """连续失败计数。达到阈值时需要告警并标记店铺重新授权。"""
    nxt = count + 1
    return nxt, nxt >= threshold


__all__ = ["evaluate_shop_health", "freshness_line", "next_refresh_failure", "resolve_sync_status"]
