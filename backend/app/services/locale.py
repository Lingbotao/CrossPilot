"""多语言文案。机翻草稿必须人工校对后才能发布，没有一键全站发布。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ParamInvalidError
from app.core.pagination import PageData, build_cursor_page, decode_cursor
from app.models.locale import (
    QUALITY_MT_DRAFT,
    QUALITY_STATUSES,
    GlossaryTerm,
    ListingContent,
    SensitiveTerm,
)
from app.repositories.listing import ListingRepository
from app.repositories.locale import GlossaryTermRepository, ListingContentRepository, SensitiveTermRepository
from app.schemas.locale import (
    GlossaryHit,
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
from app.services.locale_text import (
    apply_glossary,
    assert_publishable,
    human_status,
    scan_sensitive,
    text_empty,
)


def read_cursor(cursor: str | None) -> int | None:
    if not cursor:
        return None
    raw = decode_cursor(cursor).get("id")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text.isascii() or not text.isdigit():
        return None
    return int(text)


def content_view(row: ListingContent, replaced: list[tuple[str, str]] | None = None) -> ListingContentView:
    return ListingContentView(
        id=row.id,
        listing_id=row.listing_id,
        lang=row.lang,
        title=row.title,
        description=row.description,
        bullet_points=list(row.bullet_points or []),
        quality_status=row.quality_status,
        needs_review=row.quality_status == QUALITY_MT_DRAFT,
        replaced_terms=[GlossaryHit(source_term=source, target_term=target) for source, target in replaced or []],
    )


class LocaleService:
    def __init__(self, session: AsyncSession) -> None:
        self.contents = ListingContentRepository(session)
        self.glossary = GlossaryTermRepository(session)
        self.sensitive = SensitiveTermRepository(session)
        self.listings = ListingRepository(session)

    async def list_contents(
        self,
        *,
        limit: int,
        cursor: str | None,
        listing_id: int | None,
        lang: str | None,
        quality_status: str | None,
    ) -> PageData[ListingContentView]:
        if quality_status is not None and quality_status not in QUALITY_STATUSES:
            raise ParamInvalidError("质量状态不正确")
        rows = await self.contents.list_cursor(
            limit=limit,
            before_id=read_cursor(cursor),
            listing_id=listing_id,
            lang=lang,
            quality_status=quality_status,
        )
        return build_cursor_page([content_view(row) for row in rows], limit)

    async def save_human(self, payload: ListingContentSave, *, tenant_id: int, actor_id: int) -> ListingContentView:
        await self.listings.get_or_404(payload.listing_id)
        row = await self.contents.get_active(payload.listing_id, payload.lang)
        status = human_status(
            previous=None if row is None else row.quality_status,
            empty=text_empty(payload.title, payload.description, payload.bullet_points),
            confirm_review=payload.confirm_review,
        )
        if row is None:
            row = await self.contents.add(
                ListingContent(
                    tenant_id=tenant_id,
                    listing_id=payload.listing_id,
                    lang=payload.lang,
                    title=payload.title,
                    description=payload.description,
                    bullet_points=payload.bullet_points,
                    quality_status=status,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            return content_view(row)
        row.title = payload.title
        row.description = payload.description
        row.bullet_points = payload.bullet_points
        row.quality_status = status
        row.updated_by = actor_id
        await self.contents.save(row)
        return content_view(row)

    async def machine_draft(self, payload: MachineDraftCreate, *, tenant_id: int, actor_id: int) -> ListingContentView:
        """从源语言复制文案并强制套用术语。结果永远是机翻草稿，不会发布。"""

        await self.listings.get_or_404(payload.listing_id)
        source = await self.contents.get_active(payload.listing_id, payload.source_lang)
        if source is None or text_empty(source.title, source.description, list(source.bullet_points or [])):
            raise ParamInvalidError("源语言还没有文案")
        terms = await self.glossary.list_for_pair(payload.source_lang, payload.target_lang)
        pairs = [(item.source_term, item.target_term) for item in terms]
        title, title_hits = apply_glossary(source.title, pairs)
        description, description_hits = apply_glossary(source.description, pairs)
        bullets: list[str] = []
        bullet_hits: list[tuple[str, str]] = []
        for item in list(source.bullet_points or []):
            rewritten, hits = apply_glossary(item, pairs)
            bullets.append(rewritten)
            bullet_hits.extend(hits)
        replaced = _merge_hits(title_hits, description_hits, bullet_hits)
        row = await self.contents.get_active(payload.listing_id, payload.target_lang)
        if row is None:
            row = await self.contents.add(
                ListingContent(
                    tenant_id=tenant_id,
                    listing_id=payload.listing_id,
                    lang=payload.target_lang,
                    title=title,
                    description=description,
                    bullet_points=bullets,
                    quality_status=QUALITY_MT_DRAFT,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            return content_view(row, replaced)
        row.title = title
        row.description = description
        row.bullet_points = bullets
        row.quality_status = QUALITY_MT_DRAFT
        row.updated_by = actor_id
        await self.contents.save(row)
        return content_view(row, replaced)

    async def publish(self, content_id: int, *, actor_id: int) -> ListingContentView:
        row = await self.contents.get_or_404(content_id)
        assert_publishable(row.quality_status, row.title)
        row.quality_status = "PUBLISHED"
        row.updated_by = actor_id
        await self.contents.save(row)
        return content_view(row)

    async def remove_content(self, content_id: int, *, actor_id: int) -> ListingContentView:
        row = await self.contents.get_or_404(content_id)
        row.updated_by = actor_id
        view = content_view(row)
        await self.contents.soft_delete(row)
        return view

    async def scan(self, payload: SensitiveScan) -> list[SensitiveHit]:
        terms = await self.sensitive.list_for(payload.market, payload.lang)
        fields = [("title", payload.title), ("description", payload.description)]
        for item in payload.bullet_points:
            fields.append(("bullet", item))
        hits = scan_sensitive(fields, [(item.keyword, item.suggest_replacement) for item in terms])
        return [
            SensitiveHit(field=field, keyword=keyword, suggest_replacement=suggestion)
            for field, keyword, suggestion in hits
        ]

    async def list_glossary(
        self,
        *,
        limit: int,
        cursor: str | None,
        source_lang: str | None,
        target_lang: str | None,
        keyword: str | None,
    ) -> PageData[GlossaryTermView]:
        rows = await self.glossary.list_cursor(
            limit=limit,
            before_id=read_cursor(cursor),
            source_lang=source_lang,
            target_lang=target_lang,
            keyword=keyword.strip() if keyword else None,
        )
        return build_cursor_page([_glossary_view(row) for row in rows], limit)

    async def create_glossary(self, payload: GlossaryTermWrite, *, tenant_id: int, actor_id: int) -> GlossaryTermView:
        row = await self.glossary.add(
            GlossaryTerm(
                tenant_id=tenant_id,
                source_lang=payload.source_lang,
                source_term=payload.source_term,
                target_lang=payload.target_lang,
                target_term=payload.target_term,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        return _glossary_view(row)

    async def update_glossary(self, term_id: int, payload: GlossaryTermPatch, *, actor_id: int) -> GlossaryTermView:
        row = await self.glossary.get_or_404(term_id)
        fields = payload.model_fields_set
        if "source_lang" in fields and payload.source_lang is not None:
            row.source_lang = payload.source_lang
        if "source_term" in fields and payload.source_term is not None:
            row.source_term = payload.source_term
        if "target_lang" in fields and payload.target_lang is not None:
            row.target_lang = payload.target_lang
        if "target_term" in fields and payload.target_term is not None:
            row.target_term = payload.target_term
        row.updated_by = actor_id
        await self.glossary.save(row)
        return _glossary_view(row)

    async def remove_glossary(self, term_id: int, *, actor_id: int) -> GlossaryTermView:
        row = await self.glossary.get_or_404(term_id)
        row.updated_by = actor_id
        view = _glossary_view(row)
        await self.glossary.soft_delete(row)
        return view

    async def list_sensitive(
        self,
        *,
        limit: int,
        cursor: str | None,
        market: str | None,
        lang: str | None,
        keyword: str | None,
    ) -> PageData[SensitiveTermView]:
        rows = await self.sensitive.list_cursor(
            limit=limit,
            before_id=read_cursor(cursor),
            market=market.upper() if market else None,
            lang=lang,
            keyword=keyword.strip() if keyword else None,
        )
        return build_cursor_page([_sensitive_view(row) for row in rows], limit)

    async def create_sensitive(
        self, payload: SensitiveTermWrite, *, tenant_id: int, actor_id: int
    ) -> SensitiveTermView:
        row = await self.sensitive.add(
            SensitiveTerm(
                tenant_id=tenant_id,
                market=payload.market,
                lang=payload.lang,
                keyword=payload.keyword,
                suggest_replacement=payload.suggest_replacement,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        return _sensitive_view(row)

    async def update_sensitive(self, term_id: int, payload: SensitiveTermPatch, *, actor_id: int) -> SensitiveTermView:
        row = await self.sensitive.get_or_404(term_id)
        fields = payload.model_fields_set
        if "market" in fields and payload.market is not None:
            row.market = payload.market
        if "lang" in fields and payload.lang is not None:
            row.lang = payload.lang
        if "keyword" in fields and payload.keyword is not None:
            row.keyword = payload.keyword
        if "suggest_replacement" in fields:
            row.suggest_replacement = payload.suggest_replacement
        row.updated_by = actor_id
        await self.sensitive.save(row)
        return _sensitive_view(row)

    async def remove_sensitive(self, term_id: int, *, actor_id: int) -> SensitiveTermView:
        row = await self.sensitive.get_or_404(term_id)
        row.updated_by = actor_id
        view = _sensitive_view(row)
        await self.sensitive.soft_delete(row)
        return view


def _merge_hits(*groups: list[tuple[str, str]]) -> list[tuple[str, str]]:
    merged: list[tuple[str, str]] = []
    seen: set[str] = set()
    for group in groups:
        for source, target in group:
            key = source.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append((source, target))
    return merged


def _glossary_view(row: GlossaryTerm) -> GlossaryTermView:
    return GlossaryTermView(
        id=row.id,
        source_lang=row.source_lang,
        source_term=row.source_term,
        target_lang=row.target_lang,
        target_term=row.target_term,
    )


def _sensitive_view(row: SensitiveTerm) -> SensitiveTermView:
    return SensitiveTermView(
        id=row.id,
        market=row.market,
        lang=row.lang,
        keyword=row.keyword,
        suggest_replacement=row.suggest_replacement,
    )
