"""平台 Webhook 的受理响应。平台只看 HTTP 200，信封仍走统一格式。"""

from __future__ import annotations

from pydantic import BaseModel


class WebhookAcceptResponse(BaseModel):
    accepted: bool
    duplicate: bool = False


__all__ = ["WebhookAcceptResponse"]
