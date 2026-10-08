"""图片与商品表格的请求响应。金额不出现在这张表里。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_serializer

from app.models.product import IMAGE_TYPES


class PlatformImageCheck(BaseModel):
    platform_code: str
    ok: bool
    issues: list[str]


class ProductImageView(BaseModel):
    id: int
    spu_id: int
    sku_id: int | None
    image_type: str
    sort: int
    width_px: int
    height_px: int
    byte_size: int
    content_type: str
    white_background: bool
    compliance: list[PlatformImageCheck]

    @field_serializer("id", "spu_id")
    def _ids(self, value: int) -> str:
        return str(value)

    @field_serializer("sku_id")
    def _sku_id(self, value: int | None) -> str | None:
        if value is None:
            return None
        return str(value)


class SpreadsheetFile(BaseModel):
    filename: str
    content_type: str
    content_base64: str
    row_count: int
    truncated: bool = False


class ProductImportResult(BaseModel):
    created: int = Field(ge=0)


def image_type_or_none(value: str) -> str | None:
    text = value.strip().upper()
    if text in IMAGE_TYPES:
        return text
    return None


__all__ = [
    "PlatformImageCheck",
    "ProductImageView",
    "ProductImportResult",
    "SpreadsheetFile",
    "image_type_or_none",
]
