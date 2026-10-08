"""刊登与改价交给传输层。

fixture 返回合成的平台 ID。live 在没有 Sandbox 凭证时失败即停，不把本地写成已刊登。
"""

from __future__ import annotations

from app.adapters.base import BatchResult, CredentialView, PriceUpdate, PublishResult, UnifiedProduct
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


__all__ = ["publish_listing", "update_listing_prices"]
