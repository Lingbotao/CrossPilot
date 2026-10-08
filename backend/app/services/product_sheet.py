"""商品 Excel / CSV 导入导出。有错误行时整批不落库。不读写采购价。"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import NoReturn

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.enums import AuditAction
from app.repositories.identity import AuditLogRepository
from app.repositories.product import SkuRepository
from app.schemas.product import SkuWrite, SpuCreate
from app.schemas.product_media import ProductImportResult, SpreadsheetFile
from app.services.order_xlsx import build_xlsx
from app.services.product import ProductService
from app.services.spreadsheet import SpreadsheetError, read_tabular

MAX_IMPORT_ROWS = 500
MAX_EXPORT_ROWS = 5000
MAX_SHEET_BYTES = 2_000_000
_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("sku_code", "SKU编码"),
    ("title", "商品标题"),
    ("brand", "品牌"),
    ("barcode", "条码"),
    ("weight_g", "重量克"),
    ("length_cm", "长厘米"),
    ("width_cm", "宽厘米"),
    ("height_cm", "高厘米"),
)
_REQUIRED = ("sku_code", "title", "weight_g", "length_cm", "width_cm", "height_cm")
_HEADER_KEYS = {key: key for key, _label in _COLUMNS}
_HEADER_KEYS.update({label: key for key, label in _COLUMNS})
_HEADER_KEYS["status"] = "status"
_HEADER_KEYS["状态"] = "status"
_LABELS = dict(_COLUMNS)
RowError = dict[str, object]


class ProductSheetService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.products = ProductService(session)
        self.skus = SkuRepository(session)
        self.audit = AuditLogRepository(session)

    async def template(self) -> SpreadsheetFile:
        payload = build_xlsx([label for _key, label in _COLUMNS], [], sheet_name="products")
        return _file("products-template.xlsx", payload, row_count=0, truncated=False)

    async def export_file(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        status: str | None,
        title: str | None,
    ) -> SpreadsheetFile:
        found = await self.skus.list_for_export(limit=MAX_EXPORT_ROWS + 1, status=status, title=title)
        truncated = len(found) > MAX_EXPORT_ROWS
        visible = found[:MAX_EXPORT_ROWS]
        headers = [label for _key, label in _COLUMNS] + ["状态"]
        rows = [
            [
                sku.sku_code,
                spu.title,
                spu.brand or "",
                sku.barcode or "",
                format(sku.weight_g, "f"),
                format(sku.length_cm, "f"),
                format(sku.width_cm, "f"),
                format(sku.height_cm, "f"),
                spu.status,
            ]
            for sku, spu in visible
        ]
        payload = build_xlsx(headers, rows, sheet_name="products")
        await self.audit.append_action(
            tenant_id=tenant_id,
            action=AuditAction.DATA_EXPORT,
            resource="product",
            user_id=actor_id,
            after={"row_count": len(rows), "truncated": truncated},
        )
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        return _file(f"products-{stamp}.xlsx", payload, row_count=len(rows), truncated=truncated)

    async def import_file(
        self,
        payload: bytes,
        *,
        filename: str,
        tenant_id: int,
        actor_id: int,
    ) -> ProductImportResult:
        if len(payload) > MAX_SHEET_BYTES:
            _fail([_error(1, "", "文件过大")])
        try:
            table = read_tabular(payload, filename or "upload.xlsx")
        except SpreadsheetError as exc:
            _fail([_error(1, "", str(exc))])
        if not table:
            _fail([_error(1, "", "表格是空的")])
        header_number, header_cells = table[0]
        columns = _columns(header_cells)
        errors = [_error(header_number, key, f"缺少列：{_LABELS[key]}") for key in _REQUIRED if key not in columns]
        data = table[1:]
        if len(data) > MAX_IMPORT_ROWS:
            errors.append(_error(data[MAX_IMPORT_ROWS][0], "", f"单次最多导入 {MAX_IMPORT_ROWS} 行"))
        drafts: list[tuple[int, SpuCreate]] = []
        seen: set[str] = set()
        if not errors:
            for number, cells in data:
                row_errors, draft = _parse_row(number, cells, columns, seen)
                errors.extend(row_errors)
                if draft is not None:
                    drafts.append((number, draft))
            if not data:
                errors.append(_error(header_number, "", "没有数据行"))
        if not errors:
            for number, draft in drafts:
                if await self.skus.code_taken(draft.skus[0].sku_code):
                    errors.append(_error(number, "sku_code", "SKU 编码已存在"))
        if errors:
            _fail(errors)
        for _number, draft in drafts:
            await self.products.create_spu(
                draft,
                tenant_id=tenant_id,
                actor_id=actor_id,
                can_view_cost=False,
            )
        return ProductImportResult(created=len(drafts))


def _file(filename: str, payload: bytes, *, row_count: int, truncated: bool) -> SpreadsheetFile:
    return SpreadsheetFile(
        filename=filename,
        content_type=_CONTENT_TYPE,
        content_base64=base64.b64encode(payload).decode("ascii"),
        row_count=row_count,
        truncated=truncated,
    )


def _fail(rows: list[RowError]) -> NoReturn:
    raise AppError("导入文件有错误，没有写入任何商品", code=ErrorCode.PRODUCT_IMPORT_INVALID, data={"rows": rows})


def _error(row: int, column: str, message: str) -> RowError:
    return {"row": row, "column": column, "message": message}


def _columns(header: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for index, cell in enumerate(header):
        key = _HEADER_KEYS.get(cell.strip())
        if key and key not in found:
            found[key] = index
    return found


def _cell(row: list[str], columns: dict[str, int], key: str) -> str:
    index = columns.get(key)
    if index is None or index >= len(row):
        return ""
    return row[index].strip()


def _parse_row(
    number: int,
    cells: list[str],
    columns: dict[str, int],
    seen: set[str],
) -> tuple[list[RowError], SpuCreate | None]:
    errors: list[RowError] = []
    sku_code = _cell(cells, columns, "sku_code")
    title = _cell(cells, columns, "title")
    if not sku_code or len(sku_code) > 64:
        errors.append(_error(number, "sku_code", "SKU 编码不能为空，且不超过 64 个字符"))
    elif sku_code in seen:
        errors.append(_error(number, "sku_code", "文件里 SKU 编码重复"))
    else:
        seen.add(sku_code)
    if not title or len(title) > 256:
        errors.append(_error(number, "title", "商品标题不能为空，且不超过 256 个字符"))
    brand = _optional(cells, columns, "brand", 128, number, errors)
    barcode = _optional(cells, columns, "barcode", 64, number, errors)
    measures: dict[str, Decimal] = {}
    for key in ("weight_g", "length_cm", "width_cm", "height_cm"):
        parsed = _positive(_cell(cells, columns, key))
        if parsed is None:
            errors.append(_error(number, key, "必须是大于 0 的十进制数字"))
        else:
            measures[key] = parsed
    if errors or "weight_g" not in measures:
        return errors, None
    draft = SpuCreate(
        title=title,
        brand=brand,
        status="DRAFT",
        skus=[
            SkuWrite(
                sku_code=sku_code,
                barcode=barcode,
                weight_g=measures["weight_g"],
                length_cm=measures["length_cm"],
                width_cm=measures["width_cm"],
                height_cm=measures["height_cm"],
            )
        ],
    )
    return errors, draft


def _optional(
    cells: list[str],
    columns: dict[str, int],
    key: str,
    limit: int,
    number: int,
    errors: list[RowError],
) -> str | None:
    text = _cell(cells, columns, key)
    if not text:
        return None
    if len(text) > limit:
        errors.append(_error(number, key, f"不能超过 {limit} 个字符"))
        return None
    return text


def _positive(text: str) -> Decimal | None:
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if value <= 0:
        return None
    return value


__all__ = ["MAX_EXPORT_ROWS", "MAX_IMPORT_ROWS", "ProductSheetService"]
