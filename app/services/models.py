"""Kiểu dữ liệu và kiểm tra dùng chung cho cả hai nguồn dữ liệu (thư mục file và PostgreSQL)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from app.contracts import LegalDocument, LegalUnit

from .render_pdf import render_page

DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PERSON_COLORS = 8  # số màu phân biệt người chỉnh sửa (biến CSS --person-1 .. --person-8)
MAX_PROBLEMS_SHOWN = 50


class ImportProblem(Exception):
    """Văn bản không nhập được; `problems` là danh sách lỗi đọc được bởi người dùng."""

    def __init__(self, document_id: str, problems: list[str]):
        super().__init__(f"{document_id}: {len(problems)} lỗi")
        self.document_id = document_id
        self.problems = problems[:MAX_PROBLEMS_SHOWN]
        self.truncated = len(problems) > MAX_PROBLEMS_SHOWN


@dataclass
class DocumentSummary:
    """Một dòng ở màn hình danh sách. `problem` chỉ có ở chế độ thư mục file khi văn bản sai định dạng."""

    document_id: str
    document_no: str | None = None
    document_type: str | None = None
    title: str | None = None
    page_count: int | None = None
    unit_count: int | None = None
    problem: ImportProblem | None = None
    progress: dict | None = None  # đếm theo trạng thái thẩm định; None khi chưa có PostgreSQL
    categories: dict[str, int] = field(default_factory=dict)  # mã danh mục -> thứ tự trong danh mục (chỉ PostgreSQL)
    workflow: object | None = None  # workflow.Workflow: phân công và trạng thái quy trình (chỉ PostgreSQL)


@dataclass
class LoadedDocument:
    """Một văn bản đã nạp. `directory` chứa source.pdf và pages/pNNN.png (ảnh trang)."""

    doc: LegalDocument  # nội dung đã áp dụng các bản sửa chữ (nếu có)
    page_count: int
    directory: Path
    can_review: bool = False  # chỉ chế độ PostgreSQL mới lưu được thao tác thẩm định
    reviews: dict = field(default_factory=dict)  # unit_id -> UnitReview
    events: list = field(default_factory=list)  # mọi ReviewEvent của văn bản, theo thời gian
    pdf_sha256: str | None = None  # chế độ PostgreSQL: khóa của toạ độ chữ OCR (page_words.py)
    word_cache: object | None = None  # nơi lưu toạ độ chữ OCR; None = tệp trong thư mục bộ đệm của văn bản
    # Bản riêng của từng người (review.fold_tracks). `doc`, `reviews`, `events` ở trên là của bản đang xem (`version`).
    version: str = "final"  # "final" (bản chốt) hoặc tên người chỉnh sửa
    tracks: object | None = None  # review.Tracks: mọi bản của văn bản
    original_units: list = field(default_factory=list)  # nội dung OCR gốc, chưa áp dụng bản sửa nào
    workflow: object | None = None  # workflow.Workflow (chỉ PostgreSQL)

    @cached_property
    def units_by_id(self) -> dict[str, LegalUnit]:
        return {unit.unit_id: unit for unit in self.doc.legal_units}

    @cached_property
    def original_text(self) -> dict[str, str]:
        return {unit.unit_id: unit.content for unit in (self.original_units or self.doc.legal_units)}

    @property
    def people(self) -> list[str]:
        """Người chỉnh sửa của văn bản (màu 1, 2, ...): người được giao theo thứ tự giao, rồi người khác đã thao tác
        theo thứ tự lần thao tác đầu tiên (ví dụ người đã bị gỡ phân công)."""
        assigned = list(self.workflow.editors) if self.workflow is not None else []
        acted = self.tracks.people if self.tracks is not None else []
        return assigned + [person for person in acted if person not in assigned]

    def color_of(self, person: str) -> int | None:
        """Số màu (1..PERSON_COLORS) của một người chỉnh sửa trong văn bản này; None nếu chưa thao tác."""
        people = self.people
        return people.index(person) % PERSON_COLORS + 1 if person in people else None

    def text_of(self, unit_id: str, version: str) -> str:
        """Chữ của đơn vị trong một bản: "ocr" (bản OCR gốc), "final" (bản chốt) hoặc tên người chỉnh sửa."""
        original = self.original_text.get(unit_id, "")
        if version == "ocr" or self.tracks is None:
            return original
        state = self.tracks.reviews_of(version).get(unit_id)
        return state.edited_content if state is not None and state.edited_content is not None else original

    def events_of(self, unit_id: str) -> list:
        return [e for e in self.events if e.unit_id == unit_id]

    @property
    def pdf_path(self) -> Path:
        return self.directory / "source.pdf"

    def page_image(self, page: int) -> Path:
        """Ảnh PNG của trang `page` (đếm từ 1). Có sẵn thì dùng; chưa có thì render từ source.pdf."""
        return render_page(self.pdf_path, self.directory / "pages", page)


def check_source_pages(doc: LegalDocument, page_count: int) -> list[str]:
    """Mọi đơn vị phải có source_pages hợp lệ (không rỗng, trong 1..page_count, tăng dần)."""
    problems: list[str] = []
    for unit in doc.legal_units:
        pages = unit.source_pages
        if not pages:
            problems.append(f"{unit.unit_id}: thiếu source_pages")
        elif any(p < 1 or p > page_count for p in pages):
            problems.append(f"{unit.unit_id}: source_pages {pages} nằm ngoài 1..{page_count}")
        elif pages != sorted(pages):
            problems.append(f"{unit.unit_id}: source_pages {pages} phải tăng dần")
    return problems


def check_parents_exist(doc: LegalDocument) -> list[str]:
    """Mỗi parent_unit_id phải trỏ tới một đơn vị có trong văn bản (cây Điều → Khoản → Điểm không được đứt)."""
    known = {unit.unit_id for unit in doc.legal_units}
    return [
        f"{unit.unit_id}: đơn vị cha {unit.parent_unit_id} không có trong văn bản"
        for unit in doc.legal_units
        if unit.parent_unit_id is not None and unit.parent_unit_id not in known
    ]
