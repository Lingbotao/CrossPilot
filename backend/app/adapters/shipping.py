"""把运单回传交给各平台的发货接口。fixture 模式由传输层直接确认。"""

from __future__ import annotations

from app.adapters.base import CredentialView
from app.adapters.transport import PlatformTransport


async def post_shipment(
    transport: PlatformTransport,
    cred: CredentialView,
    *,
    url: str,
    platform_order_id: str,
    carrier: str,
    tracking_no: str,
) -> None:
    """live 模式才会带上访问令牌。签名等待 Sandbox 凭证，不在这里伪造成功。"""

    body = {
        "platform_order_id": platform_order_id,
        "carrier": carrier,
        "tracking_no": tracking_no,
        "access_token": cred.access_token,
    }
    await transport.request("POST", url, json_body=body, platform=cred.platform)


async def post_order_change(
    transport: PlatformTransport,
    cred: CredentialView,
    *,
    url: str,
    platform_order_id: str,
    fields: dict[str, object],
) -> None:
    """改址或备注。平台未确认时调用方不得改本地订单。"""

    body = {
        "platform_order_id": platform_order_id,
        "access_token": cred.access_token,
        **fields,
    }
    await transport.request("POST", url, json_body=body, platform=cred.platform)


__all__ = ["post_order_change", "post_shipment"]
