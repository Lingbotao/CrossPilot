"""HS 检索与按市场绑定的契约。响应不含税率。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.schemas.listing import parse_id
from app.schemas.locale import clean_market


class HsCodeHit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    description: str
    chapter: str
    level: int
    source: str

    @field_serializer("id")
    def _id(self, value: int) -> str:
        return str(value)


class HsBindRequest(BaseModel):
    market: str
    hs_code_id: int
    basis: str = Field(min_length=1, max_length=500)

    @field_validator("market", mode="before")
    @classmethod
    def _market(cls, value: object) -> str:
        return clean_market(value)

    @field_validator("hs_code_id", mode="before")
    @classmethod
    def _hs_code_id(cls, value: object) -> int:
        return parse_id(value)

    @field_validator("basis", mode="before")
    @classmethod
    def _basis(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("绑定依据必须填写")
        text = value.strip()
        if not text:
            raise ValueError("绑定依据必须填写")
        return text


class SpuHsBindingView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    spu_id: int
    market: str
    hs_code_id: int
    code: str
    description: str
    source: str
    basis: str
    updated_by: int | None
    updated_at: datetime

    @field_serializer("id", "spu_id", "hs_code_id")
    def _ids(self, value: int) -> str:
        return str(value)

    @field_serializer("updated_by")
    def _operator(self, value: int | None) -> str | None:
        return None if value is None else str(value)
