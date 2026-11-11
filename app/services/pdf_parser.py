"""Tự động trích xuất văn bản từ file PDF và dựng cấu trúc LegalDocument JSON nháp.

Nếu người dùng chỉ tải lên 1 file PDF (không kèm document.json):
1. Dùng pypdfium2 đọc text từng trang PDF.
2. Thử tách cấu trúc Điều / Khoản / Điểm bằng scripts.build_legal_document (nếu PDF đúng chuẩn Nghị định/Luật).
3. Nếu là Phụ lục rời (ví dụ PhuLuc1.pdf) hoặc biểu mẫu tự do: tự động gom theo Điều/Mục hoặc theo từng Trang
   để người dùng đối chiếu, sửa chữ và xuất ra file JSON chuẩn.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pypdfium2 as pdfium

from app.contracts import LegalDocument, LegalUnit, make_unit_id

_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")
_ARTICLE_HEAD = re.compile(r"(?m)^\s*Điều\s+(\d+)\s*[\.\:\-]?\s*(.*)$")
_CLAUSE_HEAD = re.compile(r"(?m)^\s*(\d+)\.\s+(.*)$")


def extract_pages_text(pdf_bytes: bytes) -> list[str]:
    """Trích xuất text thô của từng trang PDF bằng pypdfium2."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    pages: list[str] = []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            textpage = page.get_textpage()
            raw = textpage.get_text_range() or ""
            # Chuẩn hóa xuống dòng
            clean = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
            pages.append(clean)
            textpage.close()
            page.close()
    finally:
        pdf.close()
    return pages


def _make_clean_doc_id(filename: str) -> str:
    stem = Path(filename or "VAN_BAN").stem
    # Chuyển tiếng Việt có dấu sang không dấu cơ bản nếu có
    import unicodedata
    nkfd = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    cleaned = _SAFE_ID.sub("_", nkfd).strip("_")
    return (cleaned or "DOC_UPLOAD")[:60]


def auto_build_json_from_pdf(pdf_bytes: bytes, filename: str = "document.pdf") -> bytes:
    """Nhận bytes của file PDF, tự động phân tích và trả về bytes của document.json hợp lệ."""
    pages_text = extract_pages_text(pdf_bytes)
    page_count = max(len(pages_text), 1)
    doc_id = _make_clean_doc_id(filename)

    # Thử dùng bộ parser chính của dự án nếu văn bản là Nghị định đầy đủ có số hiệu chuẩn
    try:
        from scripts.build_legal_document import build_document
        raw_pages = [f"PRINTED_PAGE: {i + 1}\n\n{txt}" for i, txt in enumerate(pages_text)]
        built = build_document(raw_pages)
        doc_obj = LegalDocument.model_validate(built)
        return json.dumps(doc_obj.model_dump(mode="json"), ensure_ascii=False, indent=2).encode("utf-8")
    except Exception:
        pass

    # Fallback thông minh cho mọi file PDF bất kỳ (PhuLuc1.pdf, văn bản scan, thông tư, phụ lục...)
    first_non_empty = next((p for p in pages_text if p.strip()), "")
    first_lines = [ln.strip() for ln in first_non_empty.splitlines() if ln.strip()]
    title = first_lines[0][:180] if first_lines else f"Văn bản trích xuất từ {filename}"
    for ln in first_lines[:8]:
        if any(k in ln.upper() for k in ("PHỤ LỤC", "NGHỊ ĐỊNH", "THÔNG TƯ", "QUYẾT ĐỊNH", "LUẬT")):
            title = ln[:180]
            break

    units: list[LegalUnit] = []

    # Kiểm tra xem văn bản có chia theo "Điều 1, Điều 2..." không
    found_articles = False
    seen_articles: set[int] = set()
    for p_idx, p_txt in enumerate(pages_text, start=1):
        matches = list(_ARTICLE_HEAD.finditer(p_txt))
        if matches:
            found_articles = True
            for m_idx, m in enumerate(matches):
                art_no = int(m.group(1))
                if art_no in seen_articles:
                    continue
                seen_articles.add(art_no)
                art_title = m.group(2).strip() or f"Điều {art_no}"
                start_pos = m.end()
                end_pos = matches[m_idx + 1].start() if m_idx + 1 < len(matches) else len(p_txt)
                body = p_txt[start_pos:end_pos].strip() or art_title
                uid = make_unit_id(doc_id, art_no)
                units.append(
                    LegalUnit(
                        unit_id=uid,
                        article=art_no,
                        title=art_title,
                        content=body,
                        is_confident=True,
                        source_pages=[p_idx],
                    )
                )

    # Nếu không có "Điều N" (ví dụ PhuLuc1.pdf chỉ gồm các mục hoặc bảng biểu theo trang),
    # tự động tạo từng đơn vị theo từng trang để người dùng đối chiếu 1-1 với ảnh PDF bên trái
    if not found_articles or not units:
        units = []
        for p_idx, p_txt in enumerate(pages_text, start=1):
            content = p_txt.strip()
            is_conf = True
            if not content:
                content = f"[Trang {p_idx} là trang ảnh scan chưa có lớp chữ (text layer) — hãy nhập/sửa nội dung tại đây]"
                is_conf = False
            first_line = content.splitlines()[0].strip()[:80]
            units.append(
                LegalUnit(
                    unit_id=f"{doc_id}_TRANG_{p_idx}",
                    article=None,
                    title=f"Trang {p_idx}: {first_line}",
                    content=content,
                    is_confident=is_conf,
                    source_pages=[p_idx],
                )
            )

    doc = LegalDocument(
        document_id=doc_id,
        document_no=f"{doc_id}/2026",
        document_type="Văn bản / Phụ lục",
        title=title,
        issued_date=date.today(),
        effective_from=date.today(),
        status="active",
        legal_units=units,
    )
    return json.dumps(doc.model_dump(mode="json"), ensure_ascii=False, indent=2).encode("utf-8")
