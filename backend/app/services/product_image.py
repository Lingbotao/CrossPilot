"""商品图片。不合规只记在清单里，不拒绝保存。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.images import assess_image, max_upload_bytes
from app.core.errors import AppError, ErrorCode, ParamInvalidError
from app.db.snowflake import next_snowflake_id
from app.models.product import ProductImage
from app.object_store import ObjectStore, default_object_store
from app.repositories.product import ProductImageRepository, SkuRepository, SpuRepository
from app.schemas.product_media import PlatformImageCheck, ProductImageView, image_type_or_none
from app.services.image_file import inspect_image

_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


class ProductImageService:
    def __init__(self, session: AsyncSession, *, store: ObjectStore | None = None) -> None:
        self.session = session
        self.images = ProductImageRepository(session)
        self.spus = SpuRepository(session)
        self.skus = SkuRepository(session)
        self.store = store if store is not None else default_object_store()

    async def upload(
        self,
        body: bytes,
        *,
        spu_id: int,
        sku_id: int | None,
        image_type: str,
        sort: int,
        tenant_id: int,
        actor_id: int,
    ) -> ProductImageView:
        kind = image_type_or_none(image_type)
        if kind is None:
            raise ParamInvalidError("图片类型必须是主图、副图或 A+ 图")
        if sort < 0:
            raise ParamInvalidError("排序不能为负")
        if len(body) > max_upload_bytes():
            raise AppError("图片超过允许大小", code=ErrorCode.IMAGE_FILE_INVALID)
        await self.spus.get_or_404(spu_id)
        if sku_id is not None:
            sku = await self.skus.get_or_404(sku_id)
            if sku.spu_id != spu_id:
                raise ParamInvalidError("变体不属于该商品")
        inspected = inspect_image(body)
        compliance = assess_image(
            image_type=kind,
            width_px=inspected.width_px,
            height_px=inspected.height_px,
            byte_size=len(body),
            content_type=inspected.content_type,
            white_background=inspected.white_background,
        )
        extension = _EXTENSIONS[inspected.content_type]
        key = f"{tenant_id}/{next_snowflake_id()}.{extension}"
        stored = self.store.put(key, body, inspected.content_type)
        row = await self.images.add(
            ProductImage(
                tenant_id=tenant_id,
                spu_id=spu_id,
                sku_id=sku_id,
                object_key=stored,
                image_type=kind,
                sort=sort,
                width_px=inspected.width_px,
                height_px=inspected.height_px,
                byte_size=len(body),
                content_type=inspected.content_type,
                white_background=inspected.white_background,
                platform_compliance=compliance,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        return _view(row)

    async def list_for_spu(self, spu_id: int) -> list[ProductImageView]:
        await self.spus.get_or_404(spu_id)
        return [_view(row) for row in await self.images.list_for_spu(spu_id)]

    async def remove(self, image_id: int, *, actor_id: int) -> ProductImageView:
        row = await self.images.get_or_404(image_id)
        row.updated_by = actor_id
        view = _view(row)
        await self.images.soft_delete(row)
        return view


def _view(row: ProductImage) -> ProductImageView:
    checks: list[PlatformImageCheck] = []
    for item in row.platform_compliance:
        if not isinstance(item, dict):
            continue
        issues = item.get("issues")
        checks.append(
            PlatformImageCheck(
                platform_code=str(item.get("platform_code") or ""),
                ok=bool(item.get("ok")),
                issues=[str(issue) for issue in issues] if isinstance(issues, list) else [],
            )
        )
    return ProductImageView(
        id=row.id,
        spu_id=row.spu_id,
        sku_id=row.sku_id,
        image_type=row.image_type,
        sort=row.sort,
        width_px=row.width_px,
        height_px=row.height_px,
        byte_size=row.byte_size,
        content_type=row.content_type,
        white_background=row.white_background,
        compliance=checks,
    )


__all__ = ["ProductImageService"]
