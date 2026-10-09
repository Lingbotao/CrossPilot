"""多语言文案：分语言存储、机翻草稿不能发布、术语强制替换、敏感词只提示。"""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.adapters.locale import SITE_CONTENT_LANGUAGE, content_language
from app.adapters.sites import PLATFORM_SITES
from app.core.errors import AppError, ErrorCode, ParamInvalidError
from app.db.base import SoftDeleteMixin, TenantMixin
from app.models import TENANT_SCOPED_TABLES
from app.models.locale import (
    CONTENT_LANGUAGES,
    CONTENT_MARKETS,
    QUALITY_MT_DRAFT,
    QUALITY_PUBLISHED,
    QUALITY_REVIEWED,
    QUALITY_UNTRANSLATED,
    GlossaryTerm,
    ListingContent,
    SensitiveTerm,
)
from app.schemas.locale import ListingContentSave, MachineDraftCreate, SensitiveScan
from app.services.locale import LocaleService
from app.services.locale_text import (
    apply_glossary,
    assert_publishable,
    human_status,
    require_published_title,
    scan_sensitive,
    text_empty,
)


def test_locale_tables_are_tenant_master_data() -> None:
    for model, table in (
        (ListingContent, "listing_content"),
        (GlossaryTerm, "glossary_term"),
        (SensitiveTerm, "sensitive_term"),
    ):
        assert issubclass(model, TenantMixin)
        assert issubclass(model, SoftDeleteMixin)
        assert table in TENANT_SCOPED_TABLES
    index = next(
        item for item in ListingContent.__table__.indexes if item.name == "ix_listing_content_tenant_id_quality_status"
    )
    assert [column.name for column in index.columns] == ["tenant_id", "quality_status"]
    unique = next(item for item in ListingContent.__table__.indexes if item.name == "uq_listing_content_lang_active")
    assert [column.name for column in unique.columns] == ["tenant_id", "listing_id", "lang"]


def test_every_shop_site_has_one_content_language() -> None:
    sites = {site for group in PLATFORM_SITES.values() for site in group}
    assert sites == set(CONTENT_MARKETS)
    assert set(SITE_CONTENT_LANGUAGE) == sites
    assert set(SITE_CONTENT_LANGUAGE.values()) <= set(CONTENT_LANGUAGES)
    assert content_language("sg") == "en"
    assert content_language("ID") == "id"
    assert content_language("ZZ") is None
    assert "zh-CN" in CONTENT_LANGUAGES


def test_requests_cannot_set_quality_or_publish_every_language() -> None:
    assert "quality_status" not in ListingContentSave.model_fields
    assert "quality_status" not in MachineDraftCreate.model_fields
    payload = ListingContentSave.model_validate(
        {"listing_id": "15", "lang": "en", "title": "Cup", "quality_status": "PUBLISHED"}
    )
    assert payload.title == "Cup"
    assert "quality_status" not in payload.model_dump()
    with pytest.raises(ValidationError):
        MachineDraftCreate.model_validate({"listing_id": "15", "source_lang": "zh-CN", "target_lang": "zh-CN"})
    with pytest.raises(ValidationError):
        ListingContentSave.model_validate({"listing_id": 1.5, "lang": "en", "title": "Cup"})
    names = [name for name in dir(LocaleService) if "publish" in name and not name.startswith("_")]
    assert names == ["publish"]
    assert list(inspect.signature(LocaleService.publish).parameters) == ["self", "content_id", "actor_id"]


def test_glossary_replaces_longer_terms_first_and_sensitive_words_only_warn() -> None:
    rewritten, hits = apply_glossary("Use CrossPilot and cross", [("Cross", "叉"), ("CrossPilot", "杯牌")])
    assert rewritten == "Use 杯牌 and 叉"
    assert hits == [("CrossPilot", "杯牌"), ("Cross", "叉")]
    assert scan_sensitive([("title", "magic cure"), ("bullet", "cure all")], [("cure", "care")]) == [
        ("title", "cure", "care"),
        ("bullet", "cure", "care"),
    ]
    assert text_empty("  ", "", [" "]) is True
    assert human_status(previous=QUALITY_MT_DRAFT, empty=False, confirm_review=False) == QUALITY_MT_DRAFT
    assert human_status(previous=QUALITY_MT_DRAFT, empty=False, confirm_review=True) == QUALITY_REVIEWED
    assert human_status(previous=QUALITY_PUBLISHED, empty=False, confirm_review=False) == QUALITY_REVIEWED
    assert human_status(previous=None, empty=True, confirm_review=True) == QUALITY_UNTRANSLATED
    with pytest.raises(AppError) as blocked:
        assert_publishable(QUALITY_MT_DRAFT, "Cup")
    assert blocked.value.code == ErrorCode.CONTENT_NOT_REVIEWED
    with pytest.raises(AppError) as untitled:
        assert_publishable(QUALITY_REVIEWED, "  ")
    assert untitled.value.code == ErrorCode.CONTENT_NOT_REVIEWED
    assert_publishable(QUALITY_REVIEWED, "Cup")
    with pytest.raises(AppError) as pending:
        require_published_title(QUALITY_MT_DRAFT, "Cup", lang="en")
    assert pending.value.code == ErrorCode.CONTENT_NOT_REVIEWED
    assert require_published_title(QUALITY_PUBLISHED, " Cup ", lang="en") == "Cup"


def _service() -> LocaleService:
    service = LocaleService(MagicMock())
    service.listings = MagicMock()
    service.listings.get_or_404 = AsyncMock(return_value=SimpleNamespace(id=7))
    service.contents = MagicMock()
    service.glossary = MagicMock()
    service.sensitive = MagicMock()
    return service


async def test_machine_draft_forces_glossary_and_does_not_publish() -> None:
    service = _service()
    source = ListingContent(
        id=11,
        tenant_id=9,
        listing_id=7,
        lang="zh-CN",
        title="CrossPilot 杯",
        description="原描述",
        bullet_points=["CrossPilot 卖点"],
        quality_status=QUALITY_REVIEWED,
    )
    service.contents.get_active = AsyncMock(side_effect=lambda _listing_id, lang: source if lang == "zh-CN" else None)
    service.glossary.list_for_pair = AsyncMock(
        return_value=[SimpleNamespace(source_term="CrossPilot", target_term="杯牌")]
    )
    captured: list[ListingContent] = []

    async def add(row: ListingContent) -> ListingContent:
        row.id = 51
        captured.append(row)
        return row

    service.contents.add = AsyncMock(side_effect=add)
    view = await service.machine_draft(
        MachineDraftCreate.model_validate({"listing_id": "7", "source_lang": "zh-CN", "target_lang": "en"}),
        tenant_id=9,
        actor_id=3,
    )
    assert source.title == "CrossPilot 杯"
    assert source.quality_status == QUALITY_REVIEWED
    assert captured[0].lang == "en"
    assert captured[0].quality_status == QUALITY_MT_DRAFT
    assert captured[0].title == "杯牌 杯"
    assert captured[0].bullet_points == ["杯牌 卖点"]
    assert view.needs_review is True
    assert view.quality_status == QUALITY_MT_DRAFT
    assert view.replaced_terms[0].source_term == "CrossPilot"
    assert view.replaced_terms[0].target_term == "杯牌"


async def test_machine_draft_on_published_copy_returns_to_review() -> None:
    service = _service()
    source = ListingContent(
        id=11,
        tenant_id=9,
        listing_id=7,
        lang="zh-CN",
        title="杯子",
        description="",
        bullet_points=[],
        quality_status=QUALITY_PUBLISHED,
    )
    target = ListingContent(
        id=12,
        tenant_id=9,
        listing_id=7,
        lang="en",
        title="Published cup",
        description="",
        bullet_points=[],
        quality_status=QUALITY_PUBLISHED,
    )

    async def get_active(_listing_id: int, lang: str) -> ListingContent | None:
        if lang == "zh-CN":
            return source
        if lang == "en":
            return target
        return None

    service.contents.get_active = AsyncMock(side_effect=get_active)
    service.glossary.list_for_pair = AsyncMock(return_value=[])
    service.contents.save = AsyncMock(side_effect=lambda row: row)
    view = await service.machine_draft(
        MachineDraftCreate.model_validate({"listing_id": "7", "source_lang": "zh-CN", "target_lang": "en"}),
        tenant_id=9,
        actor_id=3,
    )
    assert target.quality_status == QUALITY_MT_DRAFT
    assert target.title == "杯子"
    assert view.needs_review is True
    assert source.quality_status == QUALITY_PUBLISHED


async def test_human_confirm_is_required_before_publish() -> None:
    service = _service()
    row = ListingContent(
        id=13,
        tenant_id=9,
        listing_id=7,
        lang="en",
        title="Draft cup",
        description="",
        bullet_points=[],
        quality_status=QUALITY_MT_DRAFT,
    )
    service.contents.get_active = AsyncMock(return_value=row)
    service.contents.save = AsyncMock(side_effect=lambda item: item)
    service.contents.get_or_404 = AsyncMock(return_value=row)
    kept = await service.save_human(
        ListingContentSave.model_validate(
            {"listing_id": "7", "lang": "en", "title": "Draft cup", "confirm_review": False}
        ),
        tenant_id=9,
        actor_id=3,
    )
    assert kept.quality_status == QUALITY_MT_DRAFT
    assert kept.needs_review is True
    with pytest.raises(AppError) as blocked:
        await service.publish(row.id, actor_id=3)
    assert blocked.value.code == ErrorCode.CONTENT_NOT_REVIEWED
    assert row.quality_status == QUALITY_MT_DRAFT
    reviewed = await service.save_human(
        ListingContentSave.model_validate(
            {"listing_id": "7", "lang": "en", "title": "Checked cup", "confirm_review": True}
        ),
        tenant_id=9,
        actor_id=3,
    )
    assert reviewed.quality_status == QUALITY_REVIEWED
    assert reviewed.needs_review is False
    published = await service.publish(row.id, actor_id=3)
    assert published.quality_status == QUALITY_PUBLISHED
    assert row.title == "Checked cup"


async def test_sensitive_hit_does_not_change_publish_rules() -> None:
    service = _service()
    row = ListingContent(
        id=14,
        tenant_id=9,
        listing_id=7,
        lang="en",
        title="cheap cure",
        description="",
        bullet_points=[],
        quality_status=QUALITY_REVIEWED,
    )
    service.contents.get_or_404 = AsyncMock(return_value=row)
    service.contents.save = AsyncMock(side_effect=lambda item: item)
    service.sensitive.list_for = AsyncMock(return_value=[SimpleNamespace(keyword="cure", suggest_replacement="care")])
    hits = await service.scan(SensitiveScan.model_validate({"market": "sg", "lang": "en", "title": "cheap cure"}))
    assert hits[0].keyword == "cure"
    assert hits[0].suggest_replacement == "care"
    assert hits[0].field == "title"
    published = await service.publish(row.id, actor_id=3)
    assert published.quality_status == QUALITY_PUBLISHED


async def test_empty_source_cannot_become_a_machine_draft() -> None:
    service = _service()
    source = ListingContent(
        id=15,
        tenant_id=9,
        listing_id=7,
        lang="zh-CN",
        title="  ",
        description="",
        bullet_points=[],
        quality_status=QUALITY_UNTRANSLATED,
    )
    service.contents.get_active = AsyncMock(return_value=source)
    with pytest.raises(ParamInvalidError):
        await service.machine_draft(
            MachineDraftCreate.model_validate({"listing_id": "7", "source_lang": "zh-CN", "target_lang": "en"}),
            tenant_id=9,
            actor_id=3,
        )
