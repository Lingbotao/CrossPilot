"""店铺授权与同步任务契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer

SyncModule = Literal["order", "product", "inventory"]
ShopHealth = Literal["green", "yellow", "red"]


class AuthUrlRequest(BaseModel):
    site_code: str = Field(min_length=2, max_length=8)


class AuthUrlResponse(BaseModel):
    url: str
    state: str
    platform: str
    site_code: str


class OAuthCallbackRequest(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=20, max_length=4096)


class ShopResponse(BaseModel):
    id: int
    platform_code: str
    site_code: str
    shop_name: str
    platform_shop_id: str
    status: int
    health: ShopHealth
    health_reason: str | None
    last_sync_at: datetime | None
    last_sync_status: int | None
    last_error: str | None
    auth_expires_at: datetime | None
    data_retain_until: datetime | None

    @field_serializer("id")
    def _serialize_id(self, value: int) -> str:
        return str(value)


class PlatformSiteCatalog(BaseModel):
    code: str
    name: str
    sites: list[str]


class SyncRequest(BaseModel):
    module: SyncModule = "order"
    since: datetime | None = None
    until: datetime | None = None


class SyncTaskResponse(BaseModel):
    id: int
    shop_id: int
    module: str
    trigger_type: int
    status: int
    started_at: datetime | None
    finished_at: datetime | None
    since: datetime | None
    until: datetime | None
    stats: dict[str, Any]
    error: str | None
    created_at: datetime

    @field_serializer("id", "shop_id")
    def _serialize_id(self, value: int) -> str:
        return str(value)


class UnbindResponse(BaseModel):
    status: str
    data_retain_until: datetime


__all__ = [
    "AuthUrlRequest",
    "AuthUrlResponse",
    "OAuthCallbackRequest",
    "PlatformSiteCatalog",
    "ShopHealth",
    "ShopResponse",
    "SyncModule",
    "SyncRequest",
    "SyncTaskResponse",
    "UnbindResponse",
]
