"""多语言文案的请求和响应。状态由服务决定，请求体里不能带质量状态。"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from app.models.locale import CONTENT_LANGUAGES, CONTENT_MARKETS
from app.schemas.listing import parse_id

_TITLE_LIMIT = 500
_DESCRIPTION_LIMIT = 20000
_BULLET_LIMIT = 500
_MAX_BULLETS = 10
_TERM_LIMIT = 128


def clean_copy(value: object, *, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("必须是字符串")
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f"不能超过 {limit} 个字符")
    return text


def clean_lang(value: object) -> str:
    if not isinstance(value, str) or value.strip() not in CONTENT_LANGUAGES:
        raise ValueError("语言不在支持列表中")
    return value.strip()


def clean_market(value: object) -> str:
    if not isinstance(value, str) or value.strip().upper() not in CONTENT_MARKETS:
        raise ValueError("市场不在支持列表中")
    return value.strip().upper()


def clean_term(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("必须是字符串")
    text = value.strip()
    if not text or len(text) > _TERM_LIMIT:
        raise ValueError("长度为 1 到 128")
    return text


def clean_optional_term(value: object) -> str | None:
    if value is None or value == "":
        return None
    return clean_term(value)


def clean_bullets(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("卖点必须是列表")
    if len(value) > _MAX_BULLETS:
        raise ValueError("卖点最多 10 条")
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("卖点必须是字符串")
        text = item.strip()
        if not text:
            continue
        if len(text) > _BULLET_LIMIT:
            raise ValueError("单条卖点不能超过 500 个字符")
        cleaned.append(text)
    return cleaned


class GlossaryHit(BaseModel):
    source_term: str
    target_term: str


class ListingContentView(BaseModel):
    id: int
    listing_id: int
    lang: str
    title: str
    description: str
    bullet_points: list[str]
    quality_status: str
    needs_review: bool
    replaced_terms: list[GlossaryHit] = Field(default_factory=list)

    @field_serializer("id", "listing_id")
    def _ids(self, value: int) -> str:
        return str(value)


class ListingContentSave(BaseModel):
    listing_id: int
    lang: str
    title: str = ""
    description: str = ""
    bullet_points: list[str] = Field(default_factory=list)
    confirm_review: bool = False

    @field_validator("listing_id", mode="before")
    @classmethod
    def _listing_id(cls, value: object) -> int:
        return parse_id(value)

    @field_validator("lang", mode="before")
    @classmethod
    def _lang(cls, value: object) -> str:
        return clean_lang(value)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value: object) -> str:
        return clean_copy(value, limit=_TITLE_LIMIT)

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: object) -> str:
        return clean_copy(value, limit=_DESCRIPTION_LIMIT)

    @field_validator("bullet_points", mode="before")
    @classmethod
    def _bullets(cls, value: object) -> list[str]:
        return clean_bullets(value)


class MachineDraftCreate(BaseModel):
    listing_id: int
    source_lang: str
    target_lang: str

    @field_validator("listing_id", mode="before")
    @classmethod
    def _listing_id(cls, value: object) -> int:
        return parse_id(value)

    @field_validator("source_lang", "target_lang", mode="before")
    @classmethod
    def _lang(cls, value: object) -> str:
        return clean_lang(value)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if self.source_lang == self.target_lang:
            raise ValueError("源语言和目标语言需要不同")
        return self


class SensitiveHit(BaseModel):
    field: str
    keyword: str
    suggest_replacement: str | None


class SensitiveScan(BaseModel):
    market: str
    lang: str
    title: str = ""
    description: str = ""
    bullet_points: list[str] = Field(default_factory=list)

    @field_validator("market", mode="before")
    @classmethod
    def _market(cls, value: object) -> str:
        return clean_market(value)

    @field_validator("lang", mode="before")
    @classmethod
    def _lang(cls, value: object) -> str:
        return clean_lang(value)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value: object) -> str:
        return clean_copy(value, limit=_TITLE_LIMIT)

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: object) -> str:
        return clean_copy(value, limit=_DESCRIPTION_LIMIT)

    @field_validator("bullet_points", mode="before")
    @classmethod
    def _bullets(cls, value: object) -> list[str]:
        return clean_bullets(value)


class GlossaryTermView(BaseModel):
    id: int
    source_lang: str
    source_term: str
    target_lang: str
    target_term: str

    @field_serializer("id")
    def _ids(self, value: int) -> str:
        return str(value)


class GlossaryTermWrite(BaseModel):
    source_lang: str
    source_term: str
    target_lang: str
    target_term: str

    @field_validator("source_lang", "target_lang", mode="before")
    @classmethod
    def _lang(cls, value: object) -> str:
        return clean_lang(value)

    @field_validator("source_term", "target_term", mode="before")
    @classmethod
    def _term(cls, value: object) -> str:
        return clean_term(value)

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if self.source_lang == self.target_lang and self.source_term.casefold() == self.target_term.casefold():
            raise ValueError("源文和译文相同，无需入库")
        return self


class GlossaryTermPatch(BaseModel):
    source_lang: str | None = None
    source_term: str | None = None
    target_lang: str | None = None
    target_term: str | None = None

    @field_validator("source_lang", "target_lang", mode="before")
    @classmethod
    def _lang(cls, value: object) -> str | None:
        if value is None:
            return None
        return clean_lang(value)

    @field_validator("source_term", "target_term", mode="before")
    @classmethod
    def _term(cls, value: object) -> str | None:
        if value is None:
            return None
        return clean_term(value)


class SensitiveTermView(BaseModel):
    id: int
    market: str
    lang: str
    keyword: str
    suggest_replacement: str | None

    @field_serializer("id")
    def _ids(self, value: int) -> str:
        return str(value)


class SensitiveTermWrite(BaseModel):
    market: str
    lang: str
    keyword: str
    suggest_replacement: str | None = None

    @field_validator("market", mode="before")
    @classmethod
    def _market(cls, value: object) -> str:
        return clean_market(value)

    @field_validator("lang", mode="before")
    @classmethod
    def _lang(cls, value: object) -> str:
        return clean_lang(value)

    @field_validator("keyword", mode="before")
    @classmethod
    def _keyword(cls, value: object) -> str:
        return clean_term(value)

    @field_validator("suggest_replacement", mode="before")
    @classmethod
    def _suggestion(cls, value: object) -> str | None:
        return clean_optional_term(value)


class SensitiveTermPatch(BaseModel):
    market: str | None = None
    lang: str | None = None
    keyword: str | None = None
    suggest_replacement: str | None = None

    @field_validator("market", mode="before")
    @classmethod
    def _market(cls, value: object) -> str | None:
        if value is None:
            return None
        return clean_market(value)

    @field_validator("lang", mode="before")
    @classmethod
    def _lang(cls, value: object) -> str | None:
        if value is None:
            return None
        return clean_lang(value)

    @field_validator("keyword", mode="before")
    @classmethod
    def _keyword(cls, value: object) -> str | None:
        if value is None:
            return None
        return clean_term(value)

    @field_validator("suggest_replacement", mode="before")
    @classmethod
    def _suggestion(cls, value: object) -> str | None:
        if value is None:
            return None
        return clean_optional_term(value)
