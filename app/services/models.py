"""Kiểu dữ liệu và kiểm tra dùng chung cho cả hai nguồn dữ liệu (thư mục file và PostgreSQL)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from app.contracts import LegalDocument, LegalUnit

from .render_pdf import render_page

DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
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


@dataclass
class LoadedDocument:
    """Một văn bản đã nạp. `directory` chứa source.pdf và pages/pNNN.png (ảnh trang)."""

    doc: LegalDocument  # nội dung đã áp dụng các bản sửa chữ (nếu có)
    page_count: int
    directory: Path
    can_review: bool = False  # chỉ chế độ PostgreSQL mới lưu được thao tác thẩm định
    reviews: dict = field(default_factory=dict)  # unit_id -> UnitReview
    events: list = field(default_factory=list)  # mọi ReviewEvent của văn bản, theo thời gian

    @cached_property
    def units_by_id(self) -> dict[str, LegalUnit]:
        return {unit.unit_id: unit for unit in self.doc.legal_units}

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
