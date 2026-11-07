"""Nguồn dữ liệu dạng thư mục file: data/<document_id>/source.pdf + document.json.

Dùng khi không cấu hình PostgreSQL (xem nhanh, hoặc khi DB chưa sẵn sàng). Cùng giao diện với DbCatalog:
list_documents() và get(document_id).
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.contracts import LegalDocument

from .models import (
    DOC_ID_RE,
    DocumentSummary,
    ImportProblem,
    LoadedDocument,
    check_parents_exist,
    check_source_pages,
)
from .render_pdf import count_pages


class DocumentStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self._cache: dict[str, tuple[tuple[int, int], LoadedDocument]] = {}

    def list_ids(self) -> list[str]:
        if not self.data_dir.is_dir():
            return []
        return [
            child.name
            for child in sorted(self.data_dir.iterdir())
            if child.is_dir()
            and DOC_ID_RE.match(child.name)
            and (child / "document.json").is_file()
            and (child / "source.pdf").is_file()
        ]

    def list_documents(self) -> list[DocumentSummary]:
        summaries = []
        for document_id in self.list_ids():
            try:
                loaded = self.get(document_id)
            except ImportProblem as problem:
                summaries.append(DocumentSummary(document_id=document_id, problem=problem))
                continue
            doc = loaded.doc
            summaries.append(
                DocumentSummary(
                    document_id=document_id,
                    document_no=doc.document_no,
                    document_type=doc.document_type,
                    title=doc.title,
                    page_count=loaded.page_count,
                    unit_count=len(doc.legal_units),
                )
            )
        return summaries

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

        problems = check_source_pages(doc, page_count) + check_parents_exist(doc)
        if problems:
            raise ImportProblem(document_id, problems)

        return LoadedDocument(doc=doc, page_count=page_count, directory=directory)
