"""Nạp văn bản từ thư mục dữ liệu: data/<document_id>/source.pdf + document.json.

Bản đầu đọc thẳng từ file (chưa có PostgreSQL). Phần còn lại của app chỉ gọi DocumentStore.list_ids() và
DocumentStore.get(), nên khi chuyển sang PostgreSQL chỉ cần thay lớp này.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.contracts import LegalDocument, LegalUnit

from .render_pdf import count_pages, render_page

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
class LoadedDocument:
    doc: LegalDocument
    page_count: int
    directory: Path

    @property
    def units_by_id(self) -> dict[str, LegalUnit]:
        return {unit.unit_id: unit for unit in self.doc.legal_units}

    @property
    def pdf_path(self) -> Path:
        return self.directory / "source.pdf"

    def page_image(self, page: int) -> Path:
        """Ảnh PNG của trang `page` (đếm từ 1); render lần đầu khi cần."""
        return render_page(self.pdf_path, self.directory / "pages", page)


def check_source_pages(doc: LegalDocument, page_count: int) -> list[str]:
    """Kiểm tra riêng của app: mọi đơn vị phải có source_pages hợp lệ."""
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


class DocumentStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self._cache: dict[str, tuple[tuple[int, int], LoadedDocument]] = {}

    def list_ids(self) -> list[str]:
        if not self.data_dir.is_dir():
            return []
        ids = []
        for child in sorted(self.data_dir.iterdir()):
            if child.is_dir() and DOC_ID_RE.match(child.name) and (child / "document.json").is_file() and (child / "source.pdf").is_file():
                ids.append(child.name)
        return ids

    def get(self, document_id: str) -> LoadedDocument:
        """Trả LoadedDocument. KeyError nếu không có văn bản; ImportProblem nếu dữ liệu sai."""
        if not DOC_ID_RE.match(document_id):
            raise KeyError(document_id)
        directory = self.data_dir / document_id
        json_path = directory / "document.json"
        pdf_path = directory / "source.pdf"
        if not json_path.is_file() or not pdf_path.is_file():
            raise KeyError(document_id)

        stamp = (json_path.stat().st_mtime_ns, pdf_path.stat().st_mtime_ns)
        cached = self._cache.get(document_id)
        if cached and cached[0] == stamp:
            return cached[1]

        loaded = self._load(document_id, directory, json_path, pdf_path)
        self._cache[document_id] = (stamp, loaded)
        return loaded

    def _load(self, document_id: str, directory: Path, json_path: Path, pdf_path: Path) -> LoadedDocument:
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImportProblem(document_id, [f"Không đọc được document.json: {exc}"]) from exc

        try:
            doc = LegalDocument.model_validate(raw)
        except ValidationError as exc:
            problems = []
            for error in exc.errors():
                where = ".".join(str(part) for part in error["loc"]) or "(văn bản)"
                problems.append(f"{where}: {error['msg']}")
            raise ImportProblem(document_id, problems) from exc

        if doc.document_id != document_id:
            raise ImportProblem(document_id, [f"document_id trong file ({doc.document_id!r}) khác tên thư mục ({document_id!r})"])

        try:
            page_count = count_pages(pdf_path)
        except Exception as exc:  # PDF hỏng hoặc có mật khẩu
            raise ImportProblem(document_id, [f"Không đọc được PDF: {exc}"]) from exc

        problems = check_source_pages(doc, page_count)
        if problems:
            raise ImportProblem(document_id, problems)

        return LoadedDocument(doc=doc, page_count=page_count, directory=directory)
