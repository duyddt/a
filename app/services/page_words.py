"""Toạ độ từng từ trên trang scan / ảnh chụp, để tô sáng đúng vùng của Điều/Khoản/Điểm.

PDF scan không có lớp chữ nên không biết chữ nằm ở đâu trên ảnh. Mô-đun này OCR ảnh trang bằng Tesseract
(`tsv`: hộp bao của từng từ, kèm khối / đoạn / dòng) trên ĐÚNG ảnh 200 DPI đã dùng khi nhập văn bản, nên chữ OCR
được gần như y hệt nội dung các đơn vị: highlight.py so khớp nội dung đơn vị với chuỗi từ này rồi tô đúng các dòng.

Kết quả là dữ liệu dẫn xuất, tính lại được, nhưng OCR tốn 1–3 giây mỗi trang nên được lưu lại:
- chế độ PostgreSQL: bảng `page_words`, khóa (sha256 của PDF, số trang), dùng chung cho mọi máy chạy app
  (máy không có Tesseract vẫn tô chính xác nếu trang đã được OCR ở nơi khác, ví dụ `python -m app.cli page-words`);
- chế độ thư mục file: tệp JSON trong thư mục bộ đệm của văn bản (`words/pNNN.json`), bỏ đi khi PDF đổi.
Trang chưa có toạ độ mà máy có Tesseract thì OCR ngay khi có người xem trang đó (mỗi trang một lần).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

Rect = tuple[float, float, float, float]
ENGINE = "tesseract-vie-200dpi"  # đổi khi đổi cách OCR (DPI, ngôn ngữ, tiền xử lý) để không dùng lẫn kết quả cũ


@dataclass(frozen=True)
class Word:
    text: str
    box: Rect  # (trái, trên, phải, dưới) chuẩn hoá theo trang, 0..1
    line: int  # số thứ tự dòng trên trang; các từ cùng dòng có cùng số


# ---------------------------------------------------------------- đọc kết quả Tesseract


def words_from_tsv(tsv: str, width: int, height: int) -> tuple[Word, ...]:
    """Các từ (theo thứ tự đọc) từ bảng TSV của Tesseract; bỏ dòng tổng hợp và từ rỗng."""
    words: list[Word] = []
    line_ids: dict[tuple[str, str, str], int] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text:
            continue
        key = (row["block_num"], row["par_num"], row["line_num"])
        line = line_ids.setdefault(key, len(line_ids))
        left, top = int(row["left"]), int(row["top"])
        right, bottom = left + int(row["width"]), top + int(row["height"])
        box = (round(left / width, 5), round(top / height, 5), round(right / width, 5), round(bottom / height, 5))
        words.append(Word(text, box, line))
    return tuple(words)


def to_json(words: tuple[Word, ...]) -> list[list]:
    return [[w.text, *w.box, w.line] for w in words]


def from_json(rows: list) -> tuple[Word, ...]:
    return tuple(Word(row[0], (row[1], row[2], row[3], row[4]), row[5]) for row in rows)


# ---------------------------------------------------------------- OCR


def ocr_ready() -> bool:
    from .pdf_parser import _tesseract_ready

    return _tesseract_ready()


PHOTO_BACKGROUND_MIN = 225  # nền bản scan thật: phân vị 5% ≥ 241 (đo trên 109 trang của bộ dữ liệu)
PHOTO_BACKGROUND_SPREAD = 25  # ... và chênh lệch sáng giữa các vùng nền ≤ 14


def _percentile(histogram: list[int], q: float) -> int:
    total, acc = sum(histogram), 0
    for value, count in enumerate(histogram):
        acc += count
        if acc >= total * q:
            return value
    return 255


def prepare_for_ocr(image):
    """Ảnh chụp tay (nền ngả màu, chỗ sáng chỗ tối): chia mỗi điểm ảnh cho độ sáng nền ước lượng tại đó, để nền thành
    trắng đều và chữ ở vùng tối vẫn đọc được. Không xoay, không cắt: toạ độ từ vẫn khớp ảnh trang đang hiển thị.

    Bản scan nền trắng đều thì trả nguyên ảnh, nên chữ OCR y như lúc nhập văn bản."""
    from PIL import Image, ImageFilter, ImageMath, ImageOps

    gray = image.convert("L")
    width, height = gray.size
    small = gray.resize((max(1, width // 16), max(1, height // 16)), Image.BILINEAR)
    background = small.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(2))  # bỏ nét chữ, giữ nền
    histogram = background.histogram()
    low, high = _percentile(histogram, 0.05), _percentile(histogram, 0.95)
    if low >= PHOTO_BACKGROUND_MIN and high - low <= PHOTO_BACKGROUND_SPREAD:
        return image
    background = background.resize((width, height), Image.BILINEAR)
    flat = ImageMath.lambda_eval(lambda a: a["min"](a["g"] * 255 / (a["b"] + 1), 255), g=gray, b=background)
    return ImageOps.autocontrast(flat.convert("L"), cutoff=(1, 0))


def ocr_image(image) -> tuple[str, tuple[Word, ...]]:
    """(chữ của trang, các từ kèm toạ độ) trong MỘT lần nhận dạng: chữ y hệt image_to_string dùng khi nhập văn bản.
    Ảnh chụp tay được cân bằng độ sáng nền trước (prepare_for_ocr)."""
    import pytesseract

    image = prepare_for_ocr(image)
    text, tsv = pytesseract.run_and_get_multiple_output(image, extensions=["txt", "tsv"], lang="vie")
    width, height = image.size
    return text, words_from_tsv(tsv, width, height)


def render_for_ocr(pdf_path: Path, page: int):
    import pypdfium2 as pdfium

    from .pdf_parser import _render_for_ocr
    from .render_pdf import PDFIUM_LOCK

    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            return _render_for_ocr(pdf[page - 1])
        finally:
            pdf.close()


# ---------------------------------------------------------------- lưu / nạp


class FileWordCache:
    """Chế độ thư mục file: words/pNNN.json cạnh ảnh trang, kèm khoá của PDF lúc OCR."""

    def __init__(self, directory: Path):
        self.directory = directory

    def get(self, key: str, page: int) -> tuple[Word, ...] | None:
        try:
            data = json.loads((self.directory / f"p{page:03d}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("key") != key or data.get("engine") != ENGINE:
            return None
        return from_json(data["words"])

    def put(self, key: str, page: int, words: tuple[Word, ...]) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            payload = {"key": key, "engine": ENGINE, "words": to_json(words)}
            (self.directory / f"p{page:03d}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            log.warning("Không lưu được toạ độ chữ OCR trang %s vào %s", page, self.directory, exc_info=True)


class DbWordCache:
    """Chế độ PostgreSQL: bảng page_words (migration 008), khóa theo sha256 của PDF."""

    def __init__(self, pool):
        self.pool = pool

    def get(self, key: str, page: int) -> tuple[Word, ...] | None:
        from app import repository

        with self.pool.connection() as conn:
            rows = repository.get_page_words(conn, key, page, ENGINE)
        return from_json(rows) if rows is not None else None

    def put(self, key: str, page: int, words: tuple[Word, ...]) -> None:
        from app import repository

        with self.pool.connection() as conn:
            repository.put_page_words(conn, key, page, ENGINE, to_json(words))


def _cache_and_key(loaded) -> tuple[object, str]:
    cache = getattr(loaded, "word_cache", None)
    key = getattr(loaded, "pdf_sha256", None)
    if cache is not None and key:
        return cache, key
    stat = loaded.pdf_path.stat()
    return FileWordCache(loaded.directory / "words"), f"{stat.st_size}:{stat.st_mtime_ns}"


def cached(loaded, page: int) -> tuple[Word, ...] | None:
    cache, key = _cache_and_key(loaded)
    return cache.get(key, page)


def store(loaded, page: int, words: tuple[Word, ...]) -> None:
    cache, key = _cache_and_key(loaded)
    cache.put(key, page, words)


_locks: dict[tuple[str, int], threading.Lock] = {}
_locks_guard = threading.Lock()


def for_page(loaded, page: int) -> tuple[Word, ...] | None:
    """Toạ độ từ của trang: lấy bản đã lưu; chưa có mà máy có Tesseract thì OCR ngay (mỗi trang chỉ một lần)."""
    words = cached(loaded, page)
    if words is not None or not ocr_ready():
        return words
    _, key = _cache_and_key(loaded)
    with _locks_guard:
        lock = _locks.setdefault((key, page), threading.Lock())
    with lock:  # hai người cùng mở một trang: người sau chờ và dùng kết quả của người trước
        words = cached(loaded, page)
        if words is None:
            _, words = ocr_image(render_for_ocr(loaded.pdf_path, page))
            store(loaded, page, words)
    return words


# ---------------------------------------------------------------- OCR bù cho văn bản đã nhập


def backfill(pool, document_ids: list[str] | None = None, workers: int | None = None, force: bool = False,
             report=print) -> dict[str, int]:
    """OCR các trang chỉ có ảnh mà chưa có toạ độ từ (văn bản nhập trước khi có tính năng này, hoặc nhập kèm
    document.json nên không phải OCR). Trả {document_id: số trang vừa OCR}. Trang có lớp chữ thì bỏ qua."""
    import time

    from app import repository

    from .pdf_parser import ocr_workers

    if not ocr_ready():
        raise RuntimeError("Máy này chưa có Tesseract kèm gói tiếng Việt 'vie' nên không OCR được")
    with pool.connection() as conn:
        ids = document_ids or [row["document_id"] for row in repository.list_documents(conn)]
    workers = workers or ocr_workers()
    done: dict[str, int] = {}
    for document_id in ids:
        with pool.connection() as conn:
            found = repository.get_pdf_with_sha(conn, document_id)
            if found is None:
                report(f"{document_id}: không có văn bản / PDF trong DB, bỏ qua")
                continue
            pdf_bytes, digest = found
            have = set() if force else repository.pages_with_words(conn, digest, ENGINE)
        started = time.perf_counter()
        done[document_id] = _ocr_pages_without_text(pool, pdf_bytes, digest, have, workers)
        report(f"{document_id}: OCR {done[document_id]} trang ({time.perf_counter() - started:.0f} giây), "
               f"{len(have)} trang đã có từ trước")
    return done


def _ocr_pages_without_text(pool, pdf_bytes: bytes, digest: str, have: set[int], workers: int) -> int:
    """OCR song song (`workers` trang cùng lúc) các trang không có lớp chữ và chưa nằm trong `have`; lưu từng trang
    ngay khi xong (dừng giữa chừng thì lần sau làm tiếp). Chỉ dựng trước tối đa 2 × workers ảnh để không tốn RAM."""
    from collections import deque
    from concurrent.futures import ThreadPoolExecutor

    import pypdfium2 as pdfium

    from app import repository

    from .pdf_parser import MIN_TEXT_CHARS, _render_for_ocr
    from .render_pdf import PDFIUM_LOCK

    waiting: deque = deque()
    count = 0

    def save_oldest() -> None:
        page, future = waiting.popleft()
        _, words = future.result()
        with pool.connection() as conn:
            repository.put_page_words(conn, digest, page, ENGINE, to_json(words))

    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(pdf_bytes)
        page_count = len(pdf)
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ocr-words") as executor:
            for index in range(page_count):
                if index + 1 in have:
                    continue
                with PDFIUM_LOCK:
                    page = pdf[index]
                    try:
                        textpage = page.get_textpage()
                        try:
                            has_text = textpage.count_chars() >= MIN_TEXT_CHARS
                        finally:
                            textpage.close()
                        image = None if has_text else _render_for_ocr(page)
                    finally:
                        page.close()
                if image is None:
                    continue
                while len(waiting) >= 2 * workers:
                    save_oldest()
                waiting.append((index + 1, executor.submit(ocr_image, image)))
                count += 1
                del image
            while waiting:
                save_oldest()
    finally:
        with PDFIUM_LOCK:
            pdf.close()
    return count


__all__ = ["ENGINE", "DbWordCache", "FileWordCache", "Word", "backfill", "cached", "for_page", "from_json", "ocr_image",
           "ocr_ready", "render_for_ocr", "store", "to_json", "words_from_tsv"]
