"""平台 Listing 与本地售价的差异清单。售价是字符串，不是 float。"""

from __future__ import annotations

from pydantic import BaseModel, field_serializer


class ListingDiffView(BaseModel):
    id: int
    listing_id: int
    shop_id: int
    shop_name: str
    sku_code: str
    field_name: str
    local_value: str | None
    remote_value: str
    status: str

    @field_serializer("id", "listing_id", "shop_id")
    def _ids(self, value: int) -> str:
        return str(value)


class ListingPatrolResult(BaseModel):
    created: int
    scanned: int


__all__ = ["ListingDiffView", "ListingPatrolResult"]
