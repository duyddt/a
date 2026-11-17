"""Render trang PDF thành ảnh PNG (pypdfium2).

Ảnh lưu ở pages_dir/pNNN.png kèm file .stamp ghi (kích thước, thời điểm sửa) của PDF lúc render.
PDF đổi (kể cả khi chép đè mà ngày sửa cũ hơn) thì .stamp lệch và ảnh cũ bị xóa.

Cả hai chế độ đều render từng trang khi có người mở (render_page). Ở chế độ PostgreSQL, thư mục ảnh chỉ là bộ đệm:
PDF gốc nằm trong DB (bảng document_pdf), xóa ảnh thì lần xem sau dựng lại.
"""
from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

import pypdfium2 as pdfium

DPI = 150
STAMP_NAME = ".stamp"

# PDFium không an toàn đa luồng (kể cả với hai tài liệu khác nhau), mà FastAPI chạy route đồng bộ trên nhiều luồng:
# mọi lời gọi pypdfium2 trong web app phải nằm trong khoá này.
PDFIUM_LOCK = threading.RLock()


def count_pages(pdf_path: Path) -> int:
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            return len(pdf)
        finally:
            pdf.close()


def page_aspect(pdf_path: Path, page: int) -> float:
    """Tỉ lệ cao / rộng của trang `page` (đếm từ 1), để khung xem giữ đúng chỗ cho ảnh trước khi ảnh tải xong."""
    try:
        stat = pdf_path.stat()
        sizes = _page_sizes(str(pdf_path), stat.st_size, stat.st_mtime_ns)
        width, height = sizes[page - 1]
        return height / width if width else 1.414
    except (OSError, IndexError, pdfium.PdfiumError):  # PDF hỏng / thiếu trang: dùng khổ A4
        return 1.414


@lru_cache(maxsize=32)
def _page_sizes(pdf_path: str, size: int, mtime_ns: int) -> tuple[tuple[float, float], ...]:
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            return tuple(tuple(pdf[index].get_size()) for index in range(len(pdf)))
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


def _render_to(pdf: pdfium.PdfDocument, index: int, out: Path, dpi: int) -> None:
    with PDFIUM_LOCK:
        page = pdf[index]
        try:
            image = page.render(scale=dpi / 72).to_pil().copy()  # bản sao: bitmap của pdfium được giải phóng ngay trong khoá
        finally:
            page.close()
    image.save(out)  # nén PNG không cần giữ khoá


def render_page(pdf_path: Path, pages_dir: Path, page: int, dpi: int = DPI) -> Path:
    """Trả đường dẫn ảnh PNG của trang `page` (đếm từ 1), render nếu chưa có."""
    _drop_stale_images(pdf_path, pages_dir)
    out = pages_dir / f"p{page:03d}.png"
    if out.is_file():
        return out
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        _render_to(pdf, page - 1, out, dpi)
    finally:
        with PDFIUM_LOCK:
            pdf.close()
    return out

