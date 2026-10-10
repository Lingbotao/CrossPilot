"""广告报表里的金额、计数和日期。平台字段名留在各适配器里。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from app.adapters.base import UnifiedAdCampaign, UnifiedAdDay, UnifiedAdKeyword
from app.adapters.errors import AdapterError, RetryDecision
from app.engines.ads import CAMPAIGN_STATUSES, CAMPAIGN_TYPES
from app.engines.landed_cost import quantize


def _fail(platform: str, message: str) -> AdapterError:
    return AdapterError(message, platform=platform, decision=RetryDecision.FAIL_FAST)


def text_field(value: object, *, platform: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(platform, f"广告字段 {field} 缺失")
    return value.strip()


def optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def count_field(value: object, *, platform: str, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(platform, f"广告字段 {field} 必须是整数")
    if value < 0:
        raise _fail(platform, f"广告字段 {field} 不能为负")
    return value


def money_field(value: object, *, platform: str, field: str) -> Decimal:
    if value is None or isinstance(value, bool | float):
        raise _fail(platform, f"广告字段 {field} 必须是十进制字符串")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise _fail(platform, f"广告字段 {field} 不是金额") from exc
    if parsed < 0:
        raise _fail(platform, f"广告字段 {field} 不能为负")
    return quantize(parsed)


def day_field(value: object, *, platform: str) -> date:
    if not isinstance(value, str):
        raise _fail(platform, "广告日期缺失")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise _fail(platform, "广告日期无法解析") from exc


def currency_field(value: object, *, platform: str) -> str:
    text = text_field(value, platform=platform, field="currency")
    if len(text) != 3:
        raise _fail(platform, "广告币种必须是 3 位字符")
    return text.upper()


def build_keyword(
    *,
    platform: str,
    keyword: str,
    impressions: int,
    clicks: int,
    spend: Decimal,
    sales: Decimal,
    orders: int,
) -> UnifiedAdKeyword:
    cleaned = keyword.strip()
    if not cleaned:
        raise _fail(platform, "关键词为空")
    return UnifiedAdKeyword(
        keyword=cleaned[:256],
        impressions=impressions,
        clicks=clicks,
        spend=spend,
        sales=sales,
        orders=orders,
    )


def build_day(
    *,
    platform: str,
    stat_date: date,
    impressions: int,
    clicks: int,
    spend: Decimal,
    sales: Decimal,
    orders: int,
    currency: str,
    keywords: tuple[UnifiedAdKeyword, ...],
) -> UnifiedAdDay:
    if clicks > impressions:
        raise _fail(platform, "点击不能大于曝光")
    return UnifiedAdDay(
        stat_date=stat_date,
        impressions=impressions,
        clicks=clicks,
        spend=spend,
        sales=sales,
        orders=orders,
        currency=currency,
        keywords=keywords,
    )


def build_campaign(
    *,
    platform: str,
    platform_campaign_id: str,
    name: str,
    campaign_type: str,
    status: str,
    currency: str,
    platform_sku_id: str | None,
    days: tuple[UnifiedAdDay, ...],
) -> UnifiedAdCampaign:
    if campaign_type not in CAMPAIGN_TYPES or status not in CAMPAIGN_STATUSES:
        raise _fail(platform, "广告活动类型或状态无法归类")
    return UnifiedAdCampaign(
        platform_campaign_id=platform_campaign_id[:128],
        name=name[:256],
        campaign_type=campaign_type,
        status=status,
        currency=currency,
        platform_sku_id=None if platform_sku_id is None else platform_sku_id[:128],
        days=days,
    )
