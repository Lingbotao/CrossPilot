"""多语言文案、术语和敏感词查询。软删除行不占语言和词条。"""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError, ErrorCode
from app.models.locale import GlossaryTerm, ListingContent, SensitiveTerm
from app.repositories.base import BaseRepository
from app.repositories.listing import _like, _unique_violation


class ListingContentRepository(BaseRepository[ListingContent]):
    model = ListingContent

    async def add(self, obj: ListingContent) -> ListingContent:
        try:
            return await super().add(obj)
        except IntegrityError as exc:
            self._reraise_lang(exc)
            raise

    async def save(self, obj: ListingContent) -> ListingContent:
        self.assert_tenant_owned(obj)
        self.session.add(obj)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            self._reraise_lang(exc)
            raise
        return obj

    @staticmethod
    def _reraise_lang(exc: IntegrityError) -> None:
        if _unique_violation(exc, "uq_listing_content_lang_active"):
            raise AppError("该语言的文案已存在", code=ErrorCode.LOCALE_TERM_DUPLICATED) from exc

    async def get_active(self, listing_id: int, lang: str) -> ListingContent | None:
        stmt = self.base_select().where(ListingContent.listing_id == listing_id, ListingContent.lang == lang)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_cursor(
        self,
        *,
        limit: int,
        before_id: int | None,
        listing_id: int | None,
        lang: str | None,
        quality_status: str | None,
    ) -> list[ListingContent]:
        stmt = self.base_select()
        if before_id is not None:
            stmt = stmt.where(ListingContent.id < before_id)
        if listing_id is not None:
            stmt = stmt.where(ListingContent.listing_id == listing_id)
        if lang:
            stmt = stmt.where(ListingContent.lang == lang)
        if quality_status:
            stmt = stmt.where(ListingContent.quality_status == quality_status)
        stmt = stmt.order_by(ListingContent.id.desc()).limit(limit + 1)
        return list((await self.session.execute(stmt)).scalars().all())


class GlossaryTermRepository(BaseRepository[GlossaryTerm]):
    model = GlossaryTerm

    async def add(self, obj: GlossaryTerm) -> GlossaryTerm:
        try:
            return await super().add(obj)
        except IntegrityError as exc:
            self._reraise(exc)
            raise

    async def save(self, obj: GlossaryTerm) -> GlossaryTerm:
        self.assert_tenant_owned(obj)
        self.session.add(obj)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            self._reraise(exc)
            raise
        return obj

    @staticmethod
    def _reraise(exc: IntegrityError) -> None:
        if _unique_violation(exc, "uq_glossary_term_active"):
            raise AppError("该术语已存在", code=ErrorCode.LOCALE_TERM_DUPLICATED) from exc

    async def list_for_pair(self, source_lang: str, target_lang: str) -> list[GlossaryTerm]:
        stmt = (
            self.base_select()
            .where(GlossaryTerm.source_lang == source_lang, GlossaryTerm.target_lang == target_lang)
            .order_by(GlossaryTerm.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_cursor(
        self,
        *,
        limit: int,
        before_id: int | None,
        source_lang: str | None,
        target_lang: str | None,
        keyword: str | None,
    ) -> list[GlossaryTerm]:
        stmt = self.base_select()
        if before_id is not None:
            stmt = stmt.where(GlossaryTerm.id < before_id)
        if source_lang:
            stmt = stmt.where(GlossaryTerm.source_lang == source_lang)
        if target_lang:
            stmt = stmt.where(GlossaryTerm.target_lang == target_lang)
        if keyword:
            pattern = _like(keyword)
            stmt = stmt.where(
                or_(
                    GlossaryTerm.source_term.ilike(pattern, escape="\\"),
                    GlossaryTerm.target_term.ilike(pattern, escape="\\"),
                )
            )
        stmt = stmt.order_by(GlossaryTerm.id.desc()).limit(limit + 1)
        return list((await self.session.execute(stmt)).scalars().all())


class SensitiveTermRepository(BaseRepository[SensitiveTerm]):
    model = SensitiveTerm

    async def add(self, obj: SensitiveTerm) -> SensitiveTerm:
        try:
            return await super().add(obj)
        except IntegrityError as exc:
            self._reraise(exc)
            raise

    async def save(self, obj: SensitiveTerm) -> SensitiveTerm:
        self.assert_tenant_owned(obj)
        self.session.add(obj)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            self._reraise(exc)
            raise
        return obj

    @staticmethod
    def _reraise(exc: IntegrityError) -> None:
        if _unique_violation(exc, "uq_sensitive_term_active"):
            raise AppError("该敏感词已存在", code=ErrorCode.LOCALE_TERM_DUPLICATED) from exc

    async def list_for(self, market: str, lang: str) -> list[SensitiveTerm]:
        stmt = (
            self.base_select()
            .where(SensitiveTerm.market == market, SensitiveTerm.lang == lang)
            .order_by(SensitiveTerm.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_cursor(
        self,
        *,
        limit: int,
        before_id: int | None,
        market: str | None,
        lang: str | None,
        keyword: str | None,
    ) -> list[SensitiveTerm]:
        stmt = self.base_select()
        if before_id is not None:
            stmt = stmt.where(SensitiveTerm.id < before_id)
        if market:
            stmt = stmt.where(SensitiveTerm.market == market)
        if lang:
            stmt = stmt.where(SensitiveTerm.lang == lang)
        if keyword:
            stmt = stmt.where(SensitiveTerm.keyword.ilike(_like(keyword), escape="\\"))
        stmt = stmt.order_by(SensitiveTerm.id.desc()).limit(limit + 1)
        return list((await self.session.execute(stmt)).scalars().all())
