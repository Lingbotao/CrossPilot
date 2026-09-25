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
    if expires_at is not None and expires_at <= now + lead:
        return "yellow", "令牌即将过期"
    if last_sync_at is None:
        return "yellow", "尚未同步"
    if last_sync_at <= now - stale:
        return "yellow", "同步数据已陈旧"
    return "green", None


def next_refresh_failure(count: int, threshold: int) -> tuple[int, bool]:
    """连续失败计数。达到阈值时需要告警并标记店铺重新授权。"""
    nxt = count + 1
    return nxt, nxt >= threshold


__all__ = ["evaluate_shop_health", "next_refresh_failure"]
