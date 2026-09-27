"""热敏面单 PDF。A6 与 100×150mm 两种版心，多单合并成一份多页文件。"""

from __future__ import annotations

_MM_TO_PT = 72 / 25.4

LABEL_SIZES_MM: dict[str, tuple[float, float]] = {
    "A6": (105.0, 148.0),
    "100x150": (100.0, 150.0),
}


def build_label_pdf(pages: list[list[str]], *, size: str) -> bytes:
    if size not in LABEL_SIZES_MM:
        raise ValueError(f"未知面单尺寸 {size}")
    if not pages:
        raise ValueError("没有可打印的面单")
    width_mm, height_mm = LABEL_SIZES_MM[size]
    width = width_mm * _MM_TO_PT
    height = height_mm * _MM_TO_PT
    return _document(pages, width=width, height=height)


def _document(pages: list[list[str]], *, width: float, height: float) -> bytes:
    font_id = 3
    page_ids = [4 + (index * 2) for index in range(len(pages))]
    content_ids = [page_id + 1 for page_id in page_ids]
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: _pages_object(page_ids),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page_id, content_id, lines in zip(page_ids, content_ids, pages, strict=True):
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.2f} {height:.2f}] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        ).encode("ascii")
        stream = _stream(lines, height=height)
        objects[content_id] = f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream"
    return _assemble(objects)


def _pages_object(page_ids: list[int]) -> bytes:
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    return f"<< /Type /Pages /Count {len(page_ids)} /Kids [{kids}] >>".encode("ascii")


def _stream(lines: list[str], *, height: float) -> bytes:
    commands = ["BT", "/F1 11 Tf"]
    y = height - 28
    for line in lines:
        commands.append(f"1 0 0 1 14 {y:.2f} Tm ({_pdf_text(line)}) Tj")
        y -= 16
    commands.append("ET")
    return "\n".join(commands).encode("latin-1", errors="replace")


def _pdf_text(value: str) -> str:
    safe = value.encode("latin-1", errors="replace").decode("latin-1")
    return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _assemble(objects: dict[int, bytes]) -> bytes:
    header = b"%PDF-1.4\n"
    chunks = [header]
    offsets = [0]
    cursor = len(header)
    for number in range(1, max(objects) + 1):
        body = objects[number]
        piece = f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
        offsets.append(cursor)
        chunks.append(piece)
        cursor += len(piece)
    xref_at = cursor
    count = max(objects) + 1
    xref = [f"xref\n0 {count}\n", "0000000000 65535 f \n"]
    xref.extend(f"{offset:010d} 00000 n \n" for offset in offsets[1:])
    trailer = f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    return b"".join(chunks) + "".join(xref).encode("ascii") + trailer.encode("ascii")


__all__ = ["LABEL_SIZES_MM", "build_label_pdf"]
