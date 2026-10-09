"""刊登与改价交给传输层。

fixture 返回合成的平台 ID。live 在没有 Sandbox 凭证时失败即停，不把本地写成已刊登。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.adapters.base import (
    BatchResult,
    CredentialView,
    InventoryUpdate,
    PriceUpdate,
    PublishResult,
    RemoteListing,
    UnifiedProduct,
)
from app.adapters.errors import AdapterError, RetryDecision
from app.adapters.transport import PlatformTransport, use_fixture_transport

_WAIT = "刊登与改价等待 Sandbox 凭证"


def _not_live(platform: str) -> None:
    if use_fixture_transport():
        return
    raise AdapterError(_WAIT, platform=platform, decision=RetryDecision.FAIL_FAST)


async def publish_listing(
    transport: PlatformTransport,
    cred: CredentialView,
    *,
    url: str,
    product: UnifiedProduct,
) -> PublishResult:
    """各平台只负责自己的 URL。响应里的两个 ID 由传输层给出。"""

    _not_live(cred.platform)
    raw = product.raw
    _status, body = await transport.request(
        "POST",
        url,
        json_body={
            "title": product.title,
            "sku_code": str(raw.get("sku_code") or ""),
            "shop_id": cred.shop_id,
            "price": str(raw.get("price") or ""),
            "currency": str(raw.get("currency") or ""),
            "access_token": cred.access_token,
        },
        platform=cred.platform,
    )
    product_id = body.get("platform_product_id")
    sku_id = body.get("platform_sku_id")
    if not isinstance(product_id, str) or not product_id or not isinstance(sku_id, str) or not sku_id:
        raise AdapterError("刊登响应缺少平台 ID", platform=cred.platform, decision=RetryDecision.FAIL_FAST)
    return PublishResult(platform_product_id=product_id, platform_sku_id=sku_id, raw=body)


async def update_listing_prices(
    transport: PlatformTransport,
    cred: CredentialView,
    *,
    url: str,
    items: list[PriceUpdate],
) -> BatchResult:
    _not_live(cred.platform)
    _status, body = await transport.request(
        "POST",
        url,
        json_body={
            "access_token": cred.access_token,
            "items": [
                {"platform_sku_id": item.platform_sku_id, "price": str(item.price), "currency": item.currency}
                for item in items
            ],
        },
        platform=cred.platform,
    )
    succeeded = body.get("succeeded")
    failed = body.get("failed")
    if not isinstance(succeeded, int) or not isinstance(failed, int):
        raise AdapterError("改价响应不完整", platform=cred.platform, decision=RetryDecision.FAIL_FAST)
    return BatchResult(succeeded=succeeded, failed=failed, raw=body)


async def update_remote_inventory(
    transport: PlatformTransport,
    cred: CredentialView,
    *,
    url: str,
    items: list[InventoryUpdate],
) -> BatchResult:
    """各平台只负责自己的 URL。数量是整数可用库存。"""

    _not_live(cred.platform)
    _status, body = await transport.request(
        "POST",
        url,
        json_body={
            "access_token": cred.access_token,
            "items": [{"platform_sku_id": item.platform_sku_id, "available": item.available} for item in items],
        },
        platform=cred.platform,
    )
    succeeded = body.get("succeeded")
    failed = body.get("failed")
    if not isinstance(succeeded, int) or not isinstance(failed, int):
        raise AdapterError("库存回传响应不完整", platform=cred.platform, decision=RetryDecision.FAIL_FAST)
    return BatchResult(succeeded=succeeded, failed=failed, raw=body)


async def fetch_remote_listing(
    transport: PlatformTransport,
    cred: CredentialView,
    *,
    url: str,
    platform_product_id: str,
    platform_sku_id: str,
) -> RemoteListing:
    """各平台只负责自己的 URL。售价必须是十进制字符串。"""

    _not_live(cred.platform)
    _status, body = await transport.request(
        "POST",
        url,
        json_body={
            "platform_product_id": platform_product_id,
            "platform_sku_id": platform_sku_id,
            "access_token": cred.access_token,
        },
        platform=cred.platform,
    )
    price_text = body.get("price")
    currency = body.get("currency")
    if not isinstance(price_text, str) or not isinstance(currency, str) or len(currency) != 3:
        raise AdapterError("平台商品响应缺少售价", platform=cred.platform, decision=RetryDecision.FAIL_FAST)
    try:
        price = Decimal(price_text)
    except InvalidOperation as exc:
        raise AdapterError("平台售价不是十进制数字", platform=cred.platform, decision=RetryDecision.FAIL_FAST) from exc
    if price < 0:
        raise AdapterError("平台售价为负", platform=cred.platform, decision=RetryDecision.FAIL_FAST)
    return RemoteListing(price=price, currency=currency.upper())


__all__ = ["fetch_remote_listing", "publish_listing", "update_listing_prices", "update_remote_inventory"]
