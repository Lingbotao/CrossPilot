"""把各平台 token / 订单报文解析成统一模型。解析失败即不可重试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.adapters.base import TokenBundle, UnifiedOrder, UnifiedOrderItem
from app.adapters.errors import AdapterError, RetryDecision


def _dec(value: object) -> Decimal:
    return Decimal(str(value))


def _dt_from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _dt_from_unix(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)


def token_bundle(
    body: dict[str, Any],
    *,
    platform: str,
    site_code: str,
    shop_name: str | None = None,
) -> TokenBundle:
    access = str(body.get("access_token") or "")
    if not access:
        raise AdapterError("平台未返回 access_token", platform=platform, decision=RetryDecision.FAIL_FAST, raw=body)
    now = datetime.now(UTC)
    expire_in = int(body.get("expire_in") or body.get("expires_in") or 3600)
    refresh_in = body.get("refresh_expires_in")
    platform_shop_id = str(body.get("shop_id") or body.get("seller_id") or f"fixture-{platform}-{site_code}")
    return TokenBundle(
        access_token=access,
        refresh_token=str(body["refresh_token"]) if body.get("refresh_token") else None,
        expires_at=now + timedelta(seconds=expire_in),
        refresh_expires_at=now + timedelta(seconds=int(refresh_in)) if refresh_in else None,
        platform_shop_id=platform_shop_id,
        shop_name=shop_name or str(body.get("shop_name") or f"{platform.upper()} {site_code}"),
        extra={"transport": "fixture" if access.startswith("fixture-") else "live"},
    )


def require_money(value: object, *, platform: str) -> Decimal:
    try:
        amount = _dec(value)
    except Exception as exc:
        raise AdapterError("订单金额无法解析", platform=platform, decision=RetryDecision.FAIL_FAST) from exc
    return amount


def amazon_order(raw: dict[str, Any], *, shop_id: str, unified_status: str) -> UnifiedOrder:
    total = raw.get("OrderTotal") or {}
    items = []
    for row in raw.get("OrderItems") or []:
        price = (row.get("ItemPrice") or {}).get("Amount", "0")
        items.append(
            UnifiedOrderItem(
                platform_sku_id=str(row.get("SellerSKU") or ""),
                platform_product_id=str(row.get("ASIN") or ""),
                quantity=int(row.get("QuantityOrdered") or 0),
                unit_price=require_money(price, platform="amazon"),
                item_name=str(row.get("Title") or ""),
            )
        )
    return UnifiedOrder(
        platform="amazon",
        shop_id=shop_id,
        platform_order_id=str(raw["AmazonOrderId"]),
        platform_status=str(raw.get("OrderStatus") or ""),
        unified_status=unified_status,
        buyer_name=(raw.get("BuyerInfo") or {}).get("BuyerName"),
        buyer_country=(raw.get("ShippingAddress") or {}).get("CountryCode"),
        currency=str(total.get("CurrencyCode") or "USD"),
        total_amount=require_money(total.get("Amount"), platform="amazon"),
        items=items,
        paid_at=_dt_from_iso(str(raw["PurchaseDate"])) if raw.get("PurchaseDate") else None,
        updated_at=_dt_from_iso(str(raw.get("LastUpdateDate") or raw["PurchaseDate"])),
        raw=raw,
    )


def shopee_order(raw: dict[str, Any], *, shop_id: str, unified_status: str) -> UnifiedOrder:
    items = [
        UnifiedOrderItem(
            platform_sku_id=str(row.get("model_id") or ""),
            platform_product_id=str(row.get("item_id") or ""),
            quantity=int(row.get("model_quantity_purchased") or 0),
            unit_price=require_money(row.get("model_discounted_price"), platform="shopee"),
            item_name=str(row.get("item_name") or ""),
        )
        for row in raw.get("item_list") or []
    ]
    return UnifiedOrder(
        platform="shopee",
        shop_id=shop_id,
        platform_order_id=str(raw["order_sn"]),
        platform_status=str(raw.get("order_status") or ""),
        unified_status=unified_status,
        buyer_name=raw.get("buyer_username"),
        buyer_country=raw.get("region"),
        currency=str(raw.get("currency") or "SGD"),
        total_amount=require_money(raw.get("total_amount"), platform="shopee"),
        items=items,
        paid_at=_dt_from_unix(int(raw["pay_time"])) if raw.get("pay_time") else None,
        updated_at=_dt_from_unix(int(raw.get("update_time") or raw.get("pay_time") or 0)),
        raw=raw,
    )


def lazada_order(raw: dict[str, Any], *, shop_id: str, unified_status: str) -> UnifiedOrder:
    statuses = raw.get("statuses") or []
    status = str(statuses[0] if statuses else "")
    items = [
        UnifiedOrderItem(
            platform_sku_id=str(row.get("sku") or ""),
            platform_product_id=str(row.get("product_id") or ""),
            quantity=int(row.get("quantity") or 0),
            unit_price=require_money(row.get("item_price"), platform="lazada"),
            item_name=str(row.get("name") or ""),
        )
        for row in raw.get("items") or []
    ]
    created = str(raw.get("created_at") or "").replace(" +0000", "+00:00").replace(" ", "T")
    updated = str(raw.get("updated_at") or raw.get("created_at") or "").replace(" +0000", "+00:00").replace(" ", "T")
    return UnifiedOrder(
        platform="lazada",
        shop_id=shop_id,
        platform_order_id=str(raw["order_id"]),
        platform_status=status,
        unified_status=unified_status,
        buyer_name=raw.get("customer_first_name"),
        buyer_country=(raw.get("address_shipping") or {}).get("country"),
        currency=str(raw.get("currency") or "SGD"),
        total_amount=require_money(raw.get("price"), platform="lazada"),
        items=items,
        paid_at=_dt_from_iso(created) if created else None,
        updated_at=_dt_from_iso(updated) if updated else datetime.now(UTC),
        raw=raw,
    )


def tiktok_order(raw: dict[str, Any], *, shop_id: str, unified_status: str) -> UnifiedOrder:
    payment = raw.get("payment") or {}
    items = [
        UnifiedOrderItem(
            platform_sku_id=str(row.get("sku_id") or ""),
            platform_product_id=str(row.get("product_id") or ""),
            quantity=int(row.get("quantity") or 0),
            unit_price=require_money(row.get("sale_price"), platform="tiktok"),
            item_name=str(row.get("product_name") or ""),
        )
        for row in raw.get("line_items") or []
    ]
    return UnifiedOrder(
        platform="tiktok",
        shop_id=shop_id,
        platform_order_id=str(raw["id"]),
        platform_status=str(raw.get("status") or ""),
        unified_status=unified_status,
        buyer_name=raw.get("buyer_nickname"),
        buyer_country=(raw.get("recipient_address") or {}).get("region_code"),
        currency=str(payment.get("currency") or "USD"),
        total_amount=require_money(payment.get("total_amount"), platform="tiktok"),
        items=items,
        paid_at=_dt_from_unix(int(raw["paid_time"])) if raw.get("paid_time") else None,
        updated_at=_dt_from_unix(int(raw.get("update_time") or raw.get("paid_time") or 0)),
        raw=raw,
    )


__all__ = [
    "amazon_order",
    "lazada_order",
    "require_money",
    "shopee_order",
    "tiktok_order",
    "token_bundle",
]
