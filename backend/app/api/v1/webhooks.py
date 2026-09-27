"""平台 Webhook 接收入口。不走登录态，验签就是凭证。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.request_info import client_ip
from app.core.response import ApiResponse, ok
from app.schemas.webhook import WebhookAcceptResponse
from app.services.webhook_ingress import WebhookIngress, callback_url_for

router = APIRouter(tags=["平台 Webhook"])


@router.post("/webhooks/{platform}", response_model=ApiResponse[WebhookAcceptResponse])
async def receive_webhook(platform: str, request: Request) -> ApiResponse[WebhookAcceptResponse]:
    body = await request.body()
    data = await WebhookIngress().accept(
        platform=platform,
        body=body,
        headers=request.headers,
        callback_url=callback_url_for(request_url=str(request.url), platform=platform.strip().lower()),
        client_ip=client_ip(request),
    )
    return ok(data)


__all__ = ["router"]
