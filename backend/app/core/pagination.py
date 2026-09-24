"""分页封装（PRD 10.1）。

约定：
- **大列表用游标分页**（订单、流水、日志）—— 深翻页不退化，且能抗并发插入导致的重复/漏行；
- **管理后台用页码分页**（用户、角色、字典）—— 需要 total 与跳页。

游标是 base64url 编码的 JSON，仅承载**排序键**，不承载权限信息 ——
权限永远在后端重新校验，不能信任客户端传回的游标内容。
"""

from __future__ import annotations

import base64
import json
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

MAX_PAGE_SIZE = 200


class PageParams(BaseModel):
    """页码分页入参。"""

    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(default=20, ge=1, le=MAX_PAGE_SIZE, description=f"每页条数，最大 {MAX_PAGE_SIZE}")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


class CursorParams(BaseModel):
    """游标分页入参。"""

    cursor: str | None = Field(default=None, description="上一页返回的 page_info.cursor")
    limit: int = Field(default=20, ge=1, le=MAX_PAGE_SIZE)

    def decoded(self) -> dict[str, Any] | None:
        return decode_cursor(self.cursor) if self.cursor else None


class PageInfo(BaseModel):
    """分页元信息。游标分页填 cursor/has_more；页码分页额外填 total/page/page_size。"""

    cursor: str | None = None
    has_more: bool = False
    total: int | None = None
    page: int | None = None
    page_size: int | None = None


class PageData(BaseModel, Generic[T]):
    items: list[T] = Field(default_factory=list)
    page_info: PageInfo = Field(default_factory=PageInfo)


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """游标损坏时返回空 dict（等价于从头开始），不抛 500。"""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))  # type: ignore[no-any-return]
    except Exception:
        return {}


def build_page(items: list[T], page: int, page_size: int, total: int) -> PageData[T]:
    return PageData[T](
        items=items,
        page_info=PageInfo(total=total, page=page, page_size=page_size, has_more=page * page_size < total),
    )


def build_cursor_page(items: list[T], limit: int, cursor_key: str = "id") -> PageData[T]:
    """按约定：多查一条判断 has_more，返回时裁掉多余那条。"""
    has_more = len(items) > limit
    visible = items[:limit]
    next_cursor: str | None = None
    if has_more and visible:
        last = visible[-1]
        raw = getattr(last, cursor_key, None)
        if raw is not None:
            next_cursor = encode_cursor({cursor_key: str(raw)})
    return PageData[T](items=visible, page_info=PageInfo(cursor=next_cursor, has_more=has_more))
