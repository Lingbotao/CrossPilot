"""报表导出的状态、成本列和流式写入。"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.core.errors import ErrorCode
from app.engines.report import (
    EXPORT_EXPIRED,
    EXPORT_FAILED,
    EXPORT_PENDING,
    EXPORT_RUNNING,
    EXPORT_SUCCEEDED,
    SkuProfitExport,
    cell_text,
    export_columns,
    next_status,
    row_cells,
    validate_request,
    write_csv,
)


def test_error_code_is_registered() -> None:
    assert ErrorCode.REPORT_EXPORT_INVALID == 70009


def test_status_moves_forward_and_can_expire() -> None:
    assert next_status(EXPORT_PENDING, EXPORT_RUNNING) == EXPORT_RUNNING
    assert next_status(EXPORT_RUNNING, EXPORT_SUCCEEDED) == EXPORT_SUCCEEDED
    assert next_status(EXPORT_RUNNING, EXPORT_FAILED) == EXPORT_FAILED
    assert next_status(EXPORT_SUCCEEDED, EXPORT_EXPIRED) == EXPORT_EXPIRED
    assert next_status(EXPORT_PENDING, EXPORT_EXPIRED) == EXPORT_EXPIRED
    with pytest.raises(ValueError):
        next_status(EXPORT_SUCCEEDED, EXPORT_RUNNING)
    with pytest.raises(ValueError):
        next_status(EXPORT_EXPIRED, EXPORT_EXPIRED)


def test_window_rejects_reversed_dates_and_long_spans() -> None:
    start = date(2026, 1, 1)
    validate_request(
        dataset="sku_profit",
        file_format="csv",
        date_from=start,
        date_to=date(2026, 1, 31),
        max_span_days=366,
    )
    with pytest.raises(ValueError, match="结束日期"):
        validate_request(
            dataset="sku_profit",
            file_format="csv",
            date_from=date(2026, 2, 1),
            date_to=start,
            max_span_days=366,
        )
    with pytest.raises(ValueError, match="允许天数"):
        validate_request(
            dataset="sku_profit",
            file_format="xlsx",
            date_from=start,
            date_to=date(2027, 1, 2),
            max_span_days=366,
        )
    with pytest.raises(ValueError, match="数据集"):
        validate_request(
            dataset="orders",
            file_format="csv",
            date_from=start,
            date_to=start,
            max_span_days=366,
        )


def test_cost_columns_stay_out_of_the_file_without_visibility(tmp_path: Path) -> None:
    hidden = Decimal("98765.432100")
    row = SkuProfitExport(
        stat_date=date(2026, 10, 1),
        sku_id=7,
        shop_id=9,
        currency="USD",
        book_currency="CNY",
        quantity=2,
        revenue=Decimal("10.000000"),
        book_revenue=Decimal("72.000000"),
        complete=True,
        cost_total=hidden,
        net_profit=hidden,
        net_margin=hidden,
        book_cost_total=hidden,
        book_net_profit=hidden,
        fx_gain=hidden,
    )
    columns = export_columns(False)
    headers = [label for _key, label in columns]
    assert "成本" not in headers
    assert "净利" not in headers
    path = tmp_path / "hidden.csv"
    write_csv(path, headers, [row_cells(row, columns)])
    text = path.read_text(encoding="utf-8-sig")
    assert "98765.432100" not in text
    assert "10.000000" in text

    visible = export_columns(True)
    shown = ",".join(row_cells(row, visible))
    assert "98765.432100" in shown


def test_cell_text_keeps_decimal_and_rejects_float() -> None:
    assert cell_text(Decimal("1.230000")) == "1.230000"
    assert cell_text(None) == ""
    assert cell_text(False) == "否"
    with pytest.raises(TypeError):
        cell_text(1.25)


def test_csv_writes_fifty_thousand_rows_within_a_minute(tmp_path: Path) -> None:
    headers = ["统计日", "SKU", "销售额"]

    def rows() -> Iterator[list[str]]:
        for index in range(50_000):
            yield ["2026-10-01", str(index), "1.000000"]

    path = tmp_path / "bulk.csv"
    started = time.perf_counter()
    count = write_csv(path, headers, rows())
    elapsed = time.perf_counter() - started
    assert count == 50_000
    assert elapsed < 60
    text = path.read_text(encoding="utf-8-sig")
    assert text.startswith("统计日,SKU,销售额")
    assert text.count("\n") == 50_001


def test_xlsx_writes_rows_without_holding_one_string(tmp_path: Path) -> None:
    from app.engines.report import FORMAT_XLSX, open_export

    path = tmp_path / "book.xlsx"
    with open_export(path, FORMAT_XLSX, ["销售额"]) as book:
        book.write(["12.340000"])
        book.write(["0.000000"])
    with ZipFile(path) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "销售额" in sheet
    assert "12.340000" in sheet
    assert "0.000000" in sheet
