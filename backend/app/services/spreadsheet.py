"""读取 xlsx 或 csv。导出仍走 ``order_xlsx.build_xlsx``，两边都把单元格当成文本。"""

from __future__ import annotations

import csv
from io import BytesIO, StringIO
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


class SpreadsheetError(Exception):
    """文件不是可读的表格。"""


def read_tabular(payload: bytes, filename: str) -> list[tuple[int, list[str]]]:
    name = filename.lower()
    if name.endswith(".csv"):
        return _read_csv(payload)
    if name.endswith(".xlsx"):
        return _read_xlsx(payload)
    raise SpreadsheetError("文件需要是 xlsx 或 csv")


def _read_csv(payload: bytes) -> list[tuple[int, list[str]]]:
    text = payload.decode("utf-8-sig")
    rows = list(csv.reader(StringIO(text)))
    return [
        (index, [cell.strip() for cell in row])
        for index, row in enumerate(rows, start=1)
        if any(cell.strip() for cell in row)
    ]


def _read_xlsx(payload: bytes) -> list[tuple[int, list[str]]]:
    try:
        with ZipFile(BytesIO(payload)) as book:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in book.namelist():
                shared = _shared_strings(book.read("xl/sharedStrings.xml"))
            sheet_name = _sheet_path(book.namelist())
            return _sheet_rows(book.read(sheet_name), shared)
    except (BadZipFile, KeyError, ElementTree.ParseError, ValueError) as exc:
        raise SpreadsheetError("表格无法解析") from exc


def _sheet_path(names: list[str]) -> str:
    preferred = "xl/worksheets/sheet1.xml"
    if preferred in names:
        return preferred
    sheets = [name for name in names if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
    if not sheets:
        raise SpreadsheetError("表格无法解析")
    return sorted(sheets)[0]


def _xml(payload: bytes) -> ElementTree.Element:
    folded = payload.upper()
    if b"<!DOCTYPE" in folded or b"<!ENTITY" in folded:
        raise SpreadsheetError("表格无法解析")
    # 上传大小有上限，并且先拒绝了外部实体声明。
    return ElementTree.fromstring(payload)  # noqa: S314


def _shared_strings(payload: bytes) -> list[str]:
    root = _xml(payload)
    values: list[str] = []
    for node in root:
        if _local(node.tag) != "si":
            continue
        parts = [item.text or "" for item in node.iter() if _local(item.tag) == "t"]
        values.append("".join(parts))
    return values


def _sheet_rows(payload: bytes, shared: list[str]) -> list[tuple[int, list[str]]]:
    root = _xml(payload)
    rows: list[tuple[int, list[str]]] = []
    for node in root.iter():
        if _local(node.tag) != "row":
            continue
        number = int(node.attrib.get("r", "0"))
        cells: dict[int, str] = {}
        for cell in node:
            if _local(cell.tag) != "c":
                continue
            column = _column_index(cell.attrib.get("r", "A1"))
            cells[column] = _cell_text(cell, shared)
        if not cells:
            continue
        width = max(cells) + 1
        rows.append((number, [cells.get(index, "") for index in range(width)]))
    return rows


def _cell_text(cell: ElementTree.Element, shared: list[str]) -> str:
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        parts = [item.text or "" for item in cell.iter() if _local(item.tag) == "t"]
        return "".join(parts).strip()
    value = ""
    for item in cell:
        if _local(item.tag) == "v":
            value = item.text or ""
            break
    if kind == "s":
        try:
            return shared[int(value)].strip()
        except (IndexError, ValueError) as exc:
            raise SpreadsheetError("表格无法解析") from exc
    return value.strip()


def _column_index(ref: str) -> int:
    letters = "".join(char for char in ref if char.isalpha())
    if not letters:
        raise SpreadsheetError("表格无法解析")
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


__all__ = ["SpreadsheetError", "read_tabular"]
