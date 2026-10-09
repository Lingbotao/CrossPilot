"""多语言文案、术语库和敏感词。跨租户 ID 由仓储返回 404。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbSession, Identity, require_permission
from app.core.pagination import MAX_PAGE_SIZE, PageData
from app.core.permissions import Perm
from app.core.response import ApiResponse, ok
from app.schemas.locale import (
    GlossaryTermPatch,
    GlossaryTermView,
    GlossaryTermWrite,
    ListingContentSave,
    ListingContentView,
    MachineDraftCreate,
    SensitiveHit,
    SensitiveScan,
    SensitiveTermPatch,
    SensitiveTermView,
    SensitiveTermWrite,
)
from app.services.locale import LocaleService

router = APIRouter(tags=["多语言"])

ProductReader = Annotated[Identity, Depends(require_permission(Perm.PRODUCT_READ))]
ProductWriter = Annotated[Identity, Depends(require_permission(Perm.PRODUCT_WRITE))]


@router.get("/listing-contents", response_model=ApiResponse[PageData[ListingContentView]], summary="多语言文案列表")
async def list_listing_contents(
    identity: ProductReader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    listing_id: Annotated[int | None, Query()] = None,
    lang: Annotated[str | None, Query(max_length=16)] = None,
    quality_status: Annotated[str | None, Query(max_length=16)] = None,
) -> ApiResponse[PageData[ListingContentView]]:
    del identity
    data = await LocaleService(session).list_contents(
        limit=limit,
        cursor=cursor,
        listing_id=listing_id,
        lang=lang,
        quality_status=quality_status,
    )
    return ok(data)


@router.post("/listing-contents", response_model=ApiResponse[ListingContentView], summary="保存人工文案")
async def save_listing_content(
    payload: ListingContentSave,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ListingContentView]:
    data = await LocaleService(session).save_human(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="文案已保存")


@router.post(
    "/listing-contents/machine-draft",
    response_model=ApiResponse[ListingContentView],
    summary="按术语库生成机翻草稿",
)
async def machine_draft_content(
    payload: MachineDraftCreate,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ListingContentView]:
    data = await LocaleService(session).machine_draft(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="已生成机翻草稿，需要人工校对后才能发布")


@router.post("/listing-contents/scan", response_model=ApiResponse[list[SensitiveHit]], summary="扫描敏感词")
async def scan_listing_content(
    payload: SensitiveScan,
    identity: ProductReader,
    session: DbSession,
) -> ApiResponse[list[SensitiveHit]]:
    del identity
    return ok(await LocaleService(session).scan(payload))


@router.post(
    "/listing-contents/{content_id}/publish",
    response_model=ApiResponse[ListingContentView],
    summary="发布一种已校对语言",
)
async def publish_listing_content(
    content_id: int,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ListingContentView]:
    data = await LocaleService(session).publish(content_id, actor_id=identity.user.id)
    return ok(data, message="该语言已发布")


@router.delete(
    "/listing-contents/{content_id}",
    response_model=ApiResponse[ListingContentView],
    summary="删除一种语言的文案",
)
async def delete_listing_content(
    content_id: int,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[ListingContentView]:
    data = await LocaleService(session).remove_content(content_id, actor_id=identity.user.id)
    return ok(data, message="文案已删除")


@router.get("/glossary-terms", response_model=ApiResponse[PageData[GlossaryTermView]], summary="术语库")
async def list_glossary_terms(
    identity: ProductReader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    source_lang: Annotated[str | None, Query(max_length=16)] = None,
    target_lang: Annotated[str | None, Query(max_length=16)] = None,
    q: Annotated[str | None, Query(max_length=128)] = None,
) -> ApiResponse[PageData[GlossaryTermView]]:
    del identity
    data = await LocaleService(session).list_glossary(
        limit=limit,
        cursor=cursor,
        source_lang=source_lang,
        target_lang=target_lang,
        keyword=q,
    )
    return ok(data)


@router.post("/glossary-terms", response_model=ApiResponse[GlossaryTermView], summary="新增术语")
async def create_glossary_term(
    payload: GlossaryTermWrite,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[GlossaryTermView]:
    data = await LocaleService(session).create_glossary(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="术语已保存")


@router.patch("/glossary-terms/{term_id}", response_model=ApiResponse[GlossaryTermView], summary="更新术语")
async def update_glossary_term(
    term_id: int,
    payload: GlossaryTermPatch,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[GlossaryTermView]:
    data = await LocaleService(session).update_glossary(term_id, payload, actor_id=identity.user.id)
    return ok(data, message="术语已更新")


@router.delete("/glossary-terms/{term_id}", response_model=ApiResponse[GlossaryTermView], summary="删除术语")
async def delete_glossary_term(
    term_id: int,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[GlossaryTermView]:
    data = await LocaleService(session).remove_glossary(term_id, actor_id=identity.user.id)
    return ok(data, message="术语已删除")


@router.get("/sensitive-terms", response_model=ApiResponse[PageData[SensitiveTermView]], summary="敏感词")
async def list_sensitive_terms(
    identity: ProductReader,
    session: DbSession,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    market: Annotated[str | None, Query(max_length=8)] = None,
    lang: Annotated[str | None, Query(max_length=16)] = None,
    q: Annotated[str | None, Query(max_length=128)] = None,
) -> ApiResponse[PageData[SensitiveTermView]]:
    del identity
    data = await LocaleService(session).list_sensitive(
        limit=limit,
        cursor=cursor,
        market=market,
        lang=lang,
        keyword=q,
    )
    return ok(data)


@router.post("/sensitive-terms", response_model=ApiResponse[SensitiveTermView], summary="新增敏感词")
async def create_sensitive_term(
    payload: SensitiveTermWrite,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[SensitiveTermView]:
    data = await LocaleService(session).create_sensitive(
        payload,
        tenant_id=identity.tenant.id,
        actor_id=identity.user.id,
    )
    return ok(data, message="敏感词已保存")


@router.patch("/sensitive-terms/{term_id}", response_model=ApiResponse[SensitiveTermView], summary="更新敏感词")
async def update_sensitive_term(
    term_id: int,
    payload: SensitiveTermPatch,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[SensitiveTermView]:
    data = await LocaleService(session).update_sensitive(term_id, payload, actor_id=identity.user.id)
    return ok(data, message="敏感词已更新")


@router.delete("/sensitive-terms/{term_id}", response_model=ApiResponse[SensitiveTermView], summary="删除敏感词")
async def delete_sensitive_term(
    term_id: int,
    identity: ProductWriter,
    session: DbSession,
    _idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApiResponse[SensitiveTermView]:
    data = await LocaleService(session).remove_sensitive(term_id, actor_id=identity.user.id)
    return ok(data, message="敏感词已删除")
