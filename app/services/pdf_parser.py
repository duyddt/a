"""Dựng document.json khi người dùng chỉ tải lên PDF (không kèm JSON).

1. PDF trùng TỪNG BYTE với source.pdf của một văn bản mẫu trong data/ thì dùng document.json đã soát của văn bản đó.
2. Ngược lại: đọc lớp chữ từng trang. Trang không có chữ thì OCR, nếu máy chủ có Tesseract tiếng Việt hoặc
   GEMINI_API_KEY. Sau đó tách cấu trúc Điều → Khoản → Điểm bằng app/services/legal_parser.py.
3. Không đọc được chữ nào (bản scan, chưa có OCR) thì từ chối nhập, nêu rõ lý do; không tạo đơn vị giữ chỗ.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
import unicodedata
import urllib.request
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import pypdfium2 as pdfium

from app.contracts import LegalDocument, LegalUnit

from .legal_parser import parse_legal_document, plausible_title
from .models import ImportProblem
from .render_pdf import PDFIUM_LOCK

log = logging.getLogger("review-app")

UNKNOWN_DOCUMENT_NO = "(chưa rõ số hiệu)"
UNKNOWN_DOCUMENT_TYPE = "Văn bản pháp lý"
MIN_TEXT_CHARS = 15  # trang có ít chữ hơn coi như ảnh scan
OCR_DPI = 200
NO_TEXT_MESSAGE = (
    "Không đọc được chữ nào trong PDF: đây là bản scan chưa có lớp chữ và máy chủ chưa OCR được "
    "(cần Tesseract kèm gói tiếng Việt 'vie', hoặc GEMINI_API_KEY). Hãy cài OCR rồi tải lại, "
    "hoặc tải kèm document.json."
)
_WINDOWS_TESSERACT = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
)
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")

# Tesseract mặc định chia MỘT trang cho nhiều luồng OpenMP mà không nhanh hơn (đo trong Docker: 2,46 và 2,45 giây/trang),
# chỉ chiếm thêm CPU. App OCR nhiều trang song song (ocr_workers), nên mỗi tiến trình tesseract chỉ cần một luồng.
# Người vận hành đã tự đặt biến này thì giữ nguyên.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")


def _seed_dir() -> Path:
    """Thư mục dữ liệu mẫu (biến DATA_DIR trong .env / môi trường; mặc định <dự án>/data)."""
    from app.config import load_settings

    return load_settings().data_dir


def _find_matching_seed_json(pdf_bytes: bytes) -> bytes | None:
    """document.json của văn bản mẫu có source.pdf trùng SHA-256 với tệp tải lên.

    Chỉ khớp theo nội dung tệp: hai PDF cùng số trang có thể là hai văn bản hoàn toàn khác nhau."""
    data_dir = _seed_dir()
    if not data_dir.is_dir():
        return None
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    for folder in sorted(data_dir.iterdir()):
        seed_pdf, seed_json = folder / "source.pdf", folder / "document.json"
        if seed_pdf.is_file() and seed_json.is_file() and hashlib.sha256(seed_pdf.read_bytes()).hexdigest() == pdf_sha:
            return seed_json.read_bytes()
    return None


# ---------------------------------------------------------------- OCR

@lru_cache(maxsize=1)
def _tesseract_ready() -> bool:
    """Có pytesseract, có chương trình tesseract và có gói ngôn ngữ 'vie'. Kiểm tra một lần cho mỗi tiến trình."""
    try:
        import pytesseract
    except ImportError:
        return False
    if shutil.which("tesseract") is None:
        found = next((path for path in _WINDOWS_TESSERACT if os.path.isfile(path)), None)
        if found is None:
            return False
        pytesseract.pytesseract.tesseract_cmd = found
    try:
        languages = pytesseract.get_languages(config="")
    except Exception as exc:  # tesseract hỏng / không chạy được
        log.warning("Không dùng được Tesseract: %s", type(exc).__name__)
        return False
    if "vie" not in languages:
        log.warning("Tesseract chưa có gói tiếng Việt 'vie' (đang có: %s)", ", ".join(languages))
        return False
    return True


def _gemini_key() -> str:
    from app.config import read_env_file

    return (os.environ.get("GEMINI_API_KEY") or read_env_file().get("GEMINI_API_KEY") or "").strip()


def ocr_available() -> bool:
    return _tesseract_ready() or bool(_gemini_key())


def _ocr_page_image(pil_image) -> tuple[str, tuple | None]:
    """(chữ, toạ độ từng từ) của ảnh một trang. Tesseract (offline) cho cả hai trong một lần nhận dạng (page_words.py);
    không được thì Gemini Vision (nếu có khóa), chỉ có chữ."""
    if _tesseract_ready():
        try:
            from .page_words import ocr_image

            text, words = ocr_image(pil_image)
            if text and text.strip():
                return text.strip(), words
        except Exception as exc:
            log.warning("Tesseract lỗi khi OCR một trang: %s", type(exc).__name__)

    api_key = _gemini_key()
    if not api_key:
        return "", None
    try:
        buffer = io.BytesIO()
        pil_image.save(buffer, format="PNG")
        payload = {
            "contents": [{
                "parts": [
                    {"text": "Hãy chép lại chính xác từng chữ nguyên văn tiếng Việt trong trang văn bản pháp luật này, "
                             "giữ nguyên xuống dòng, tuyệt đối không bình luận thêm:"},
                    {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(buffer.getvalue()).decode("ascii")}},
                ]
            }]
        }
        # Khóa gửi qua header (không đặt trong URL để không lộ trong log của proxy/máy chủ trung gian)
        request = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["candidates"][0]["content"]["parts"][0]["text"].strip(), None
    except Exception as exc:
        log.warning("Gemini OCR lỗi khi đọc một trang: %s", type(exc).__name__)
        return "", None


def _clean(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def ocr_workers() -> int:
    """Số trang OCR cùng lúc: biến môi trường OCR_WORKERS, mặc định min(4, số nhân CPU).

    Mỗi trang là một tiến trình tesseract một luồng (OMP_THREAD_LIMIT=1), nên thêm luồng là nhanh thêm, đến khi hết nhân
    CPU hoặc hết RAM (mỗi ảnh trang 200 DPI khoảng 12 MB khi đang chờ OCR)."""
    try:
        workers = int(os.environ.get("OCR_WORKERS", ""))
    except ValueError:
        workers = 0
    return workers if workers > 0 else min(4, os.cpu_count() or 1)


def _render_for_ocr(page):
    """Ảnh trang để OCR. Gọi trong PDFIUM_LOCK; trả bản sao để bitmap của pdfium được giải phóng ngay trong khóa."""
    return page.render(scale=OCR_DPI / 72).to_pil().copy()


def _page_text(pdf, index: int, use_ocr: bool):
    """(chữ trong lớp chữ của trang, ảnh trang nếu cần OCR hoặc None)."""
    with PDFIUM_LOCK:
        page = pdf[index]
        try:
            textpage = page.get_textpage()
            try:
                text = _clean(textpage.get_text_range() or "")
            finally:
                textpage.close()
            image = _render_for_ocr(page) if len(text) < MIN_TEXT_CHARS and use_ocr else None
        finally:
            page.close()
    return text, image


def extract_pages_text(pdf_bytes: bytes, words_out: dict | None = None) -> list[str]:
    """Chữ của từng trang. Trang gần như không có lớp chữ thì OCR (chỉ khi máy chủ có OCR; không thì để trống).

    `words_out` (nếu có) nhận {số trang: toạ độ từng từ} của các trang đã OCR, để lưu lại cho việc tô sáng.

    Mọi lệnh pdfium chạy trong PDFIUM_LOCK (pdfium không an toàn đa luồng) và trên luồng gọi hàm này; OCR chạy song
    song trên ocr_workers() luồng, ngoài khóa. Chỉ dựng trước tối đa 2 × số luồng ảnh để văn bản dài không chiếm hết RAM.
    """
    use_ocr = ocr_available()
    workers = ocr_workers()
    pages: list[str] = []
    waiting: deque[tuple[int, Future]] = deque()  # (trang, kết quả OCR), theo thứ tự trang

    def collect_oldest() -> None:
        index, future = waiting.popleft()
        text, words = future.result()
        pages[index] = _clean(text) or pages[index]
        if words and words_out is not None:
            words_out[index + 1] = words

    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(pdf_bytes)
        page_count = len(pdf)
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ocr") as executor:
            for index in range(page_count):
                while len(waiting) >= 2 * workers:
                    collect_oldest()
                text, image = _page_text(pdf, index, use_ocr)
                pages.append(text)
                if image is not None:
                    waiting.append((index, executor.submit(_ocr_page_image, image)))
                    del image  # ảnh chỉ còn trong hàng đợi OCR, xong là được giải phóng
            while waiting:
                collect_oldest()
    finally:
        with PDFIUM_LOCK:
            pdf.close()
    return pages


# ---------------------------------------------------------------- dựng document.json

def document_id_from_filename(filename: str) -> str:
    """Mã văn bản cho PDF tải lên không kèm JSON: tên tệp bỏ dấu, chỉ giữ chữ, số, gạch dưới, gạch ngang."""
    stem = Path(filename or "VAN_BAN").stem
    ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    cleaned = _SAFE_ID.sub("_", ascii_stem).strip("_")
    return (cleaned or "DOC_UPLOAD")[:60]


def _page_units(doc_id: str, pages_text: list[str]) -> list[LegalUnit]:
    """Văn bản có chữ nhưng không có Điều nào (bảng, danh mục...): mỗi trang một đơn vị để người soát xem."""
    units = []
    for page, text in enumerate(pages_text, start=1):
        content = text.strip() or f"[Trang {page} không đọc được chữ]"
        units.append(LegalUnit(
            unit_id=f"{doc_id}_TRANG_{page}",
            title=f"Trang {page}: {content.splitlines()[0].strip()[:80]}",
            content=content,
            is_confident=False,  # chưa tách được cấu trúc Điều/Khoản/Điểm
            source_pages=[page],
        ))
    return units


def build_document_from_pages(pages_text: list[str], filename: str = "document.pdf") -> bytes:
    """Dựng document.json từ chữ của từng trang (pages_text[i] là trang i + 1). Không có chữ thì ImportProblem."""
    doc_id = document_id_from_filename(filename)
    parsed = parse_legal_document(pages_text, doc_id)
    if not parsed.has_text:
        raise ImportProblem(doc_id, [NO_TEXT_MESSAGE])

    meta = parsed.metadata
    first_line = next((line.strip() for text in pages_text for line in text.splitlines() if line.strip()), "")
    # không đọc được tên: dùng "Luật số 109/2025/QH15" chứ không lấy quốc hiệu hay chữ rác OCR của dòng đầu
    numbered = f"{meta.document_type} số {meta.document_no}" if meta.document_type and meta.document_no else None
    doc = LegalDocument(
        document_id=doc_id,
        document_no=meta.document_no or UNKNOWN_DOCUMENT_NO,
        document_type=meta.document_type or UNKNOWN_DOCUMENT_TYPE,
        title=(meta.title or numbered or (first_line[:180] if plausible_title(first_line) else None)
               or f"Văn bản trích xuất từ {filename}"),
        issued_date=meta.issued_date,
        effective_from=meta.effective_from,
        legal_units=parsed.units or _page_units(doc_id, pages_text),
    )
    return json.dumps(doc.model_dump(mode="json"), ensure_ascii=False, indent=2).encode("utf-8")


def auto_build_json_from_pdf(pdf_bytes: bytes, filename: str = "document.pdf", words_out: dict | None = None) -> bytes:
    """Nhận bytes của file PDF, trả về bytes của document.json (xem docstring của module).

    `words_out` nhận toạ độ từng từ của các trang phải OCR (xem extract_pages_text), để Importer lưu cho việc tô sáng."""
    matched_seed = _find_matching_seed_json(pdf_bytes)
    if matched_seed is not None:
        return matched_seed
    return build_document_from_pages(extract_pages_text(pdf_bytes, words_out), filename)
