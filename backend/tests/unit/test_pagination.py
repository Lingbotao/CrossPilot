"""分页与 ID 生成。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.core.pagination import (
    MAX_PAGE_SIZE,
    CursorParams,
    PageParams,
    build_cursor_page,
    build_page,
    decode_cursor,
    encode_cursor,
)


class TestPageParams:
    def test_offset_and_limit(self) -> None:
        params = PageParams(page=3, page_size=20)
        assert params.offset == 40
        assert params.limit == 20

    def test_page_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            PageParams(page=0)

    def test_page_size_upper_bound(self) -> None:
        """上限必须有：没有上限的 page_size 等于给数据库开了个 DoS 入口。"""
        with pytest.raises(ValueError):
            PageParams(page_size=MAX_PAGE_SIZE + 1)


class TestCursorCodec:
    def test_roundtrip(self) -> None:
        payload = {"id": "123456789"}
        assert decode_cursor(encode_cursor(payload)) == payload

    def test_cursor_is_url_safe(self) -> None:
        cursor = encode_cursor({"id": "1", "title": "含中文 与/符号"})
        assert "/" not in cursor and "+" not in cursor and "=" not in cursor

    def test_corrupted_cursor_returns_empty_dict(self) -> None:
        """游标损坏不该 500 —— 等价于从头开始，用户无感。"""
        assert decode_cursor("!!!not-base64!!!") == {}
        assert decode_cursor("") == {}

    def test_cursor_params_decoded(self) -> None:
        assert CursorParams(cursor=encode_cursor({"id": "9"})).decoded() == {"id": "9"}

    def test_cursor_params_without_cursor(self) -> None:
        assert CursorParams().decoded() is None


class TestPageBuilders:
    def test_page_builder_sets_total_and_has_more(self) -> None:
        page = build_page([1, 2, 3], page=1, page_size=3, total=10)
        assert page.page_info.total == 10
        assert page.page_info.has_more is True
        assert page.page_info.page == 1

    def test_page_builder_last_page(self) -> None:
        page = build_page([1], page=4, page_size=3, total=10)
        assert page.page_info.has_more is False

    @dataclass
    class Row:
        id: int

    def test_cursor_page_trims_extra_row(self) -> None:
        """多查一条判断 has_more，返回时必须裁掉。"""
        rows = [self.Row(1), self.Row(2), self.Row(3)]
        page = build_cursor_page(rows, limit=2)
        assert [r.id for r in page.items] == [1, 2]
        assert page.page_info.has_more is True
        assert page.page_info.cursor is not None
        assert decode_cursor(page.page_info.cursor) == {"id": "2"}

    def test_cursor_page_no_more(self) -> None:
        page = build_cursor_page([self.Row(1)], limit=2)
        assert page.page_info.has_more is False
        assert page.page_info.cursor is None

    def test_cursor_page_empty(self) -> None:
        page = build_cursor_page([], limit=20)
        assert page.items == []
        assert page.page_info.has_more is False
