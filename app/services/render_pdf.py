"""Render từng trang PDF thành ảnh PNG (pypdfium2), theo yêu cầu từng trang.

Ảnh lưu ở pages_dir/pNNN.png kèm file .stamp ghi (kích thước, thời điểm sửa) của PDF lúc render.
PDF đổi (kể cả khi chép đè mà ngày sửa cũ hơn) thì .stamp lệch và ảnh cũ bị xóa.
"""
from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

DPI = 150
STAMP_NAME = ".stamp"


def count_pages(pdf_path: Path) -> int:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(pdf)
    finally:
        pdf.close()


def _pdf_stamp(pdf_path: Path) -> str:
    stat = pdf_path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _drop_stale_images(pdf_path: Path, pages_dir: Path) -> None:
    pages_dir.mkdir(parents=True, exist_ok=True)
    stamp_file = pages_dir / STAMP_NAME
    stamp = _pdf_stamp(pdf_path)
    current = stamp_file.read_text(encoding="utf-8") if stamp_file.is_file() else None
    if current != stamp:
        for old in pages_dir.glob("p*.png"):
            old.unlink()
        stamp_file.write_text(stamp, encoding="utf-8")


def render_page(pdf_path: Path, pages_dir: Path, page: int, dpi: int = DPI) -> Path:
    """Trả đường dẫn ảnh PNG của trang `page` (đếm từ 1), render nếu chưa có."""
    _drop_stale_images(pdf_path, pages_dir)
    out = pages_dir / f"p{page:03d}.png"
    if out.is_file():
        return out
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        pdf_page = pdf[page - 1]
        try:
            pdf_page.render(scale=dpi / 72).to_pil().save(out)
        finally:
            pdf_page.close()
    finally:
        pdf.close()
    return out
