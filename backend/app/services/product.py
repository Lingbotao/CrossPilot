"""商品主数据。采购价只对可看成本的角色读写，状态变更写入审计日志。"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, ParamInvalidError, PermissionDeniedError
from app.core.pagination import PageData, build_cursor_page, decode_cursor
from app.models.enums import AuditAction
from app.models.product import MAX_SKUS_PER_SPU, PRODUCT_STATUSES, Sku, Spu
from app.repositories.identity import AuditLogRepository
from app.repositories.product import SkuRepository, SpuRepository
from app.schemas.product import (
    SkuPatch,
    SkuView,
    SkuWrite,
    SpuCreate,
    SpuDetail,
    SpuListItem,
    SpuPatch,
    cost_fields_sent,
)


def assert_variant_room(existing: int, adding: int) -> None:
    if adding < 0:
        raise ParamInvalidError("变体数量不正确")
    if existing + adding > MAX_SKUS_PER_SPU:
        raise AppError(
            f"一个商品最多 {MAX_SKUS_PER_SPU} 个 SKU 变体",
            code=ErrorCode.SPU_VARIANT_LIMIT,
        )


def assert_distinct_sku_codes(codes: list[str]) -> None:
    seen: set[str] = set()
    for code in codes:
        if code in seen:
            raise AppError("SKU 编码重复", code=ErrorCode.SKU_CODE_DUPLICATED, data={"sku_code": code})
        seen.add(code)


def resolve_cost_write(
    *,
    can_view_cost: bool,
    fields_set: set[str],
    current_price: Decimal | None,
    current_currency: str | None,
    incoming_price: Decimal | None,
    incoming_currency: str | None,
) -> tuple[Decimal | None, str | None]:
    """没有成本权限时，请求里只要带了采购价或币种就拒绝，不能静默丢掉。"""
    if not cost_fields_sent(fields_set):
        return current_price, current_currency
    if not can_view_cost:
        raise PermissionDeniedError("无权查看或修改采购价")
    price = incoming_price if "purchase_price" in fields_set else current_price
    currency = incoming_currency if "currency" in fields_set else current_currency
    if (price is None) != (currency is None):
        raise ParamInvalidError("采购价和币种需要同时填写")
    return price, currency


def visible_cost(
    price: Decimal | None,
    currency: str | None,
    *,
    can_view_cost: bool,
) -> tuple[Decimal | None, str | None]:
    if not can_view_cost:
        return None, None
    return price, currency


def read_spu_cursor(cursor: str | None) -> int | None:
    if not cursor:
        return None
    raw = decode_cursor(cursor).get("id")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text.isascii() or not text.isdigit():
        return None
    return int(text)


class ProductService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.spus = SpuRepository(session)
        self.skus = SkuRepository(session)
        self.audit = AuditLogRepository(session)

    async def list_spus(
        self,
        *,
        limit: int,
        cursor: str | None,
        status: str | None,
        title: str | None,
    ) -> PageData[SpuListItem]:
        if status is not None and status not in PRODUCT_STATUSES:
            raise ParamInvalidError("状态不正确")
        keyword = title.strip() if title else None
        rows = await self.spus.list_cursor(
            limit=limit,
            before_id=read_spu_cursor(cursor),
            status=status,
            title=keyword or None,
        )
        counts = await self.skus.counts_for([row.id for row in rows])
        items = [
            SpuListItem(
                id=row.id,
                title=row.title,
                brand=row.brand,
                status=row.status,
                sku_count=counts.get(row.id, 0),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
        return build_cursor_page(items, limit)

    async def get_spu(self, spu_id: int, *, can_view_cost: bool) -> SpuDetail:
        spu = await self.spus.get_or_404(spu_id)
        return await self._detail(spu, can_view_cost=can_view_cost)

    async def create_spu(
        self,
        payload: SpuCreate,
        *,
        tenant_id: int,
        actor_id: int,
        can_view_cost: bool,
    ) -> SpuDetail:
        assert_variant_room(0, len(payload.skus))
        assert_distinct_sku_codes([sku.sku_code for sku in payload.skus])
        for sku in payload.skus:
            await self._ensure_code_free(sku.sku_code)
        spu = Spu(
            tenant_id=tenant_id,
            title=payload.title,
            brand=payload.brand,
            material=payload.material,
            purpose=payload.purpose,
            category_code=payload.category_code,
            status=payload.status,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.spus.add(spu)
        for sku in payload.skus:
            await self.skus.add(
                self._new_sku(
                    sku,
                    spu_id=spu.id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    can_view_cost=can_view_cost,
                )
            )
        return await self._detail(spu, can_view_cost=can_view_cost)

    async def update_spu(
        self,
        spu_id: int,
        payload: SpuPatch,
        *,
        tenant_id: int,
        actor_id: int,
        can_view_cost: bool,
    ) -> SpuDetail:
        spu = await self.spus.get_or_404(spu_id)
        if "title" in payload.model_fields_set:
            if not payload.title:
                raise ParamInvalidError("标题不能为空")
            spu.title = payload.title
        for field in ("brand", "material", "purpose", "category_code"):
            if field in payload.model_fields_set:
                setattr(spu, field, getattr(payload, field))
        if "status" in payload.model_fields_set and payload.status and payload.status != spu.status:
            before = spu.status
            spu.status = payload.status
            await self.audit.append_action(
                tenant_id=tenant_id,
                user_id=actor_id,
                action=AuditAction.PRODUCT_STATUS,
                resource="spu",
                resource_id=spu.id,
                before={"status": before},
                after={"status": payload.status},
            )
        spu.updated_by = actor_id
        await self.session.flush()
        # onupdate=now() expires updated_at; a sync read then raises MissingGreenlet.
        await self.session.refresh(spu)
        return await self._detail(spu, can_view_cost=can_view_cost)

    async def add_sku(
        self,
        spu_id: int,
        payload: SkuWrite,
        *,
        tenant_id: int,
        actor_id: int,
        can_view_cost: bool,
    ) -> SkuView:
        spu = await self.spus.get_or_404(spu_id)
        assert_variant_room(await self.skus.count_for_spu(spu.id), 1)
        await self._ensure_code_free(payload.sku_code)
        sku = await self.skus.add(
            self._new_sku(payload, spu_id=spu.id, tenant_id=tenant_id, actor_id=actor_id, can_view_cost=can_view_cost)
        )
        return self._sku_view(sku, can_view_cost=can_view_cost)

    async def update_sku(
        self,
        sku_id: int,
        payload: SkuPatch,
        *,
        actor_id: int,
        can_view_cost: bool,
    ) -> SkuView:
        sku = await self.skus.get_or_404(sku_id)
        if "sku_code" in payload.model_fields_set:
            if not payload.sku_code:
                raise ParamInvalidError("SKU 编码不能为空")
            if payload.sku_code != sku.sku_code:
                await self._ensure_code_free(payload.sku_code, exclude_id=sku.id)
            sku.sku_code = payload.sku_code
        if "barcode" in payload.model_fields_set:
            sku.barcode = payload.barcode
        if "spec_attrs" in payload.model_fields_set and payload.spec_attrs is not None:
            sku.spec_attrs = payload.spec_attrs
        for field in ("weight_g", "length_cm", "width_cm", "height_cm"):
            if field not in payload.model_fields_set:
                continue
            value = getattr(payload, field)
            if value is None:
                raise ParamInvalidError("重量和尺寸不能为空")
            setattr(sku, field, value)
        price, currency = resolve_cost_write(
            can_view_cost=can_view_cost,
            fields_set=set(payload.model_fields_set),
            current_price=sku.purchase_price,
            current_currency=sku.currency,
            incoming_price=payload.purchase_price,
            incoming_currency=payload.currency,
        )
        sku.purchase_price = price
        sku.currency = currency
        sku.updated_by = actor_id
        await self.session.flush()
        await self.session.refresh(sku)
        return self._sku_view(sku, can_view_cost=can_view_cost)

    async def _ensure_code_free(self, sku_code: str, *, exclude_id: int | None = None) -> None:
        if await self.skus.code_taken(sku_code, exclude_id=exclude_id):
            raise AppError("SKU 编码重复", code=ErrorCode.SKU_CODE_DUPLICATED, data={"sku_code": sku_code})

    async def _detail(self, spu: Spu, *, can_view_cost: bool) -> SpuDetail:
        skus = await self.skus.list_for_spu(spu.id)
        return SpuDetail(
            id=spu.id,
            title=spu.title,
            brand=spu.brand,
            material=spu.material,
            purpose=spu.purpose,
            category_code=spu.category_code,
            status=spu.status,
            skus=[self._sku_view(sku, can_view_cost=can_view_cost) for sku in skus],
            created_at=spu.created_at,
            updated_at=spu.updated_at,
        )

    def _new_sku(
        self,
        payload: SkuWrite,
        *,
        spu_id: int,
        tenant_id: int,
        actor_id: int,
        can_view_cost: bool,
    ) -> Sku:
        price, currency = resolve_cost_write(
            can_view_cost=can_view_cost,
            fields_set=set(payload.model_fields_set),
            current_price=None,
            current_currency=None,
            incoming_price=payload.purchase_price,
            incoming_currency=payload.currency,
        )
        return Sku(
            tenant_id=tenant_id,
            spu_id=spu_id,
            sku_code=payload.sku_code,
            barcode=payload.barcode,
            spec_attrs=payload.spec_attrs,
            weight_g=payload.weight_g,
            length_cm=payload.length_cm,
            width_cm=payload.width_cm,
            height_cm=payload.height_cm,
            purchase_price=price,
            currency=currency,
            created_by=actor_id,
            updated_by=actor_id,
        )

    def _sku_view(self, sku: Sku, *, can_view_cost: bool) -> SkuView:
        price, currency = visible_cost(sku.purchase_price, sku.currency, can_view_cost=can_view_cost)
        view = SkuView.model_validate(sku)
        return view.model_copy(update={"purchase_price": price, "currency": currency})
