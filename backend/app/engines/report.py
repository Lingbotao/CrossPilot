"""报表导出的状态、列和文件写入。纯函数，不访问数据库。"""

from __future__ import annotations

import csv
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, TextIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from app.engines.landed_cost import money_text

EXPORT_PENDING: Final[str] = "PENDING"
EXPORT_RUNNING: Final[str] = "RUNNING"
EXPORT_SUCCEEDED: Final[str] = "SUCCEEDED"
EXPORT_FAILED: Final[str] = "FAILED"
EXPORT_EXPIRED: Final[str] = "EXPIRED"
EXPORT_STATUSES: Final[tuple[str, ...]] = (
    EXPORT_PENDING,
    EXPORT_RUNNING,
    EXPORT_SUCCEEDED,
    EXPORT_FAILED,
    EXPORT_EXPIRED,
)

DATASET_SKU_PROFIT: Final[str] = "sku_profit"
EXPORT_DATASETS: Final[tuple[str, ...]] = (DATASET_SKU_PROFIT,)
FORMAT_CSV: Final[str] = "csv"
FORMAT_XLSX: Final[str] = "xlsx"
EXPORT_FORMATS: Final[tuple[str, ...]] = (FORMAT_CSV, FORMAT_XLSX)

_FORWARD: Final[dict[str, frozenset[str]]] = {
    EXPORT_PENDING: frozenset({EXPORT_RUNNING}),
    EXPORT_RUNNING: frozenset({EXPORT_SUCCEEDED, EXPORT_FAILED}),
}

_BASE_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("stat_date", "统计日"),
    ("sku_id", "SKU"),
    ("shop_id", "店铺"),
    ("currency", "币种"),
    ("book_currency", "本位币"),
    ("quantity", "数量"),
    ("revenue", "销售额"),
    ("book_revenue", "本位币销售额"),
    ("complete", "是否完整"),
)
_COST_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("cost_total", "成本"),
    ("net_profit", "净利"),
    ("net_margin", "净利率"),
    ("book_cost_total", "本位币成本"),
    ("book_net_profit", "本位币净利"),
    ("fx_gain", "汇兑损益"),
)


@dataclass(frozen=True, slots=True)
class SkuProfitExport:
    """一行 SKU 日利润。成本字段可以有值，写文件时由列清单决定是否落下。"""

    stat_date: date
    sku_id: int
    shop_id: int
    currency: str
    book_currency: str
    quantity: int
    revenue: Decimal
    book_revenue: Decimal | None
    complete: bool
    cost_total: Decimal | None = None
    net_profit: Decimal | None = None
    net_margin: Decimal | None = None
    book_cost_total: Decimal | None = None
    book_net_profit: Decimal | None = None
    fx_gain: Decimal | None = None


def next_status(current: str, target: str) -> str:
    """PENDING 进入 RUNNING，RUNNING 进入成功或失败。未过期的任务都可以标成过期。"""

    if current not in EXPORT_STATUSES or target not in EXPORT_STATUSES:
        raise ValueError("导出状态无效")
    if target == EXPORT_EXPIRED:
        if current == EXPORT_EXPIRED:
            raise ValueError("导出任务已经过期")
        return EXPORT_EXPIRED
    if target not in _FORWARD.get(current, frozenset()):
        raise ValueError("导出状态不能这样变更")
    return target


def validate_request(
    *,
    dataset: str,
    file_format: str,
    date_from: date,
    date_to: date,
    max_span_days: int,
) -> None:
    """日期、格式和数据集不合法时抛 ValueError，由服务转成 70009。"""

    if dataset not in EXPORT_DATASETS:
        raise ValueError("不支持的导出数据集")
    if file_format not in EXPORT_FORMATS:
        raise ValueError("导出格式只支持 csv 或 xlsx")
    if max_span_days < 1:
        raise ValueError("导出区间上限无效")
    if date_from > date_to:
        raise ValueError("结束日期不能早于开始日期")
    if (date_to - date_from).days + 1 > max_span_days:
        raise ValueError("导出区间超过允许天数")


def export_columns(include_cost: bool) -> tuple[tuple[str, str], ...]:
    """没有成本可见性时不输出成本、净利、净利率和汇兑损益。"""

    if include_cost:
        return _BASE_COLUMNS + _COST_COLUMNS
    return _BASE_COLUMNS


def cell_text(value: object) -> str:
    """单元格只接受十进制、整数、日期、布尔和字符串。拒绝 float。"""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, Decimal):
        return money_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    raise TypeError(f"无法写入导出单元格：{type(value).__name__}")


def row_cells(row: SkuProfitExport, columns: tuple[tuple[str, str], ...]) -> list[str]:
    return [cell_text(getattr(row, key)) for key, _label in columns]


def content_type(file_format: str) -> str:
    if file_format == FORMAT_CSV:
        return "text/csv; charset=utf-8"
    if file_format == FORMAT_XLSX:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    raise ValueError("导出格式只支持 csv 或 xlsx")


def export_filename(date_from: date, date_to: date, file_format: str) -> str:
    return f"sku-profit-{date_from:%Y%m%d}-{date_to:%Y%m%d}.{file_format}"


def object_key(tenant_id: int, export_id: int, file_format: str) -> str:
    return f"{tenant_id}/{export_id}.{file_format}"


class ExportFile:
    """按行写入临时文件。退出时若没有异常，xlsx 才打包，避免把半截表交出去。"""

    def __init__(self, path: Path, file_format: str, headers: list[str]) -> None:
        self.path = path
        self.file_format = file_format
        self.headers = headers
        self.count = 0
        self._csv: Any = None
        self._handle: TextIO | None = None
        self._body: Path | None = None

    def __enter__(self) -> ExportFile:
        if self.file_format == FORMAT_CSV:
            handle = self.path.open("w", encoding="utf-8-sig", newline="")
            self._handle = handle
            self._csv = csv.writer(handle)
            self._csv.writerow(self.headers)
            return self
        if self.file_format == FORMAT_XLSX:
            self._body = self.path.with_suffix(".sheet-body.xml")
            handle = self._body.open("w", encoding="utf-8")
            self._handle = handle
            handle.write(_inline_row(1, self.headers))
            return self
        raise ValueError("导出格式只支持 csv 或 xlsx")

    def write(self, values: list[str]) -> None:
        self.count += 1
        if self._csv is not None:
            self._csv.writerow(values)
            return
        handle = self._handle
        if handle is None:
            raise RuntimeError("导出文件还没有打开")
        handle.write(_inline_row(self.count + 1, values))

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc, traceback
        if exc_type is None:
            self._finalize()
        else:
            self._discard()

    def _finalize(self) -> None:
        if self.file_format == FORMAT_CSV:
            self._close_handle()
            return
        self._close_handle()
        body = self._body
        if body is None:
            raise RuntimeError("缺少工作表正文")
        sheet = self.path.with_suffix(".sheet.xml")
        last_col = _column_name(max(len(self.headers), 1))
        last_row = max(self.count + 1, 1)
        with sheet.open("w", encoding="utf-8") as out, body.open(encoding="utf-8") as src:
            out.write(
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<dimension ref="A1:{last_col}{last_row}"/>'
                "<sheetData>"
            )
            shutil.copyfileobj(src, out)
            out.write("</sheetData></worksheet>")
        with ZipFile(self.path, "w", compression=ZIP_DEFLATED) as book:
            book.writestr("[Content_Types].xml", _CONTENT_TYPES)
            book.writestr("_rels/.rels", _ROOT_RELS)
            book.writestr("xl/workbook.xml", _workbook("sku-profit"))
            book.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
            book.write(sheet, "xl/worksheets/sheet1.xml")
        body.unlink(missing_ok=True)
        sheet.unlink(missing_ok=True)

    def _discard(self) -> None:
        self._close_handle()
        if self._body is not None:
            self._body.unlink(missing_ok=True)

    def _close_handle(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            handle.close()


def open_export(path: Path, file_format: str, headers: list[str]) -> ExportFile:
    return ExportFile(path, file_format, headers)


def write_csv(path: Path, headers: list[str], rows: Iterable[list[str]]) -> int:
    """给测试和同步调用方用的整表写入。"""

    with open_export(path, FORMAT_CSV, headers) as book:
        for row in rows:
            book.write(row)
        return book.count


def _inline_row(index: int, values: list[str]) -> str:
    """单元格形态与订单 xlsx 一致：inlineStr + XML 转义，不把整表拼成一个字符串。"""

    cells: list[str] = []
    for column, value in enumerate(values, start=1):
        ref = f"{_column_name(column)}{index}"
        cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
    return f'<row r="{index}">{"".join(cells)}</row>'


def _column_name(index: int) -> str:
    name = ""
    current = index
    while current:
        current, remainder = divmod(current - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _workbook(sheet_name: str) -> str:
    safe = escape(sheet_name)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{safe}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>
"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""


__all__ = [
    "DATASET_SKU_PROFIT",
    "EXPORT_DATASETS",
    "EXPORT_EXPIRED",
    "EXPORT_FAILED",
    "EXPORT_FORMATS",
    "EXPORT_PENDING",
    "EXPORT_RUNNING",
    "EXPORT_STATUSES",
    "EXPORT_SUCCEEDED",
    "FORMAT_CSV",
    "FORMAT_XLSX",
    "ExportFile",
    "SkuProfitExport",
    "cell_text",
    "content_type",
    "export_columns",
    "export_filename",
    "next_status",
    "object_key",
    "open_export",
    "row_cells",
    "validate_request",
    "write_csv",
]
