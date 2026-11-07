"""Nguồn dữ liệu dạng thư mục file: data/<document_id>/source.pdf + document.json.

Dùng khi không cấu hình PostgreSQL (xem nhanh, hoặc khi DB chưa sẵn sàng). Cùng giao diện với DbCatalog:
list_documents() và get(document_id).
Khi can_review=True, hỗ trợ lưu trữ thẩm định dạng tệp (reviews.json).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
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
from .review import ReviewEvent, ReviewProblem, apply_edits, decide, fold_events, progress


class DocumentStore:
    def __init__(self, data_dir: str | Path, can_review: bool = False, storage_dir: str | Path | None = None):
        self.data_dir = Path(data_dir)
        self.can_review = can_review
        self.storage_dir = Path(storage_dir) if storage_dir else self.data_dir
        self._cache: dict[str, tuple[tuple[int, int], LoadedDocument]] = {}
        self._events: dict[str, list[ReviewEvent]] = {}
        if self.can_review:
            self.record_review = self._record_review
            self._load_persisted_events()

    def _reviews_file(self, document_id: str) -> Path:
        target_dir = self.storage_dir / document_id
        if not target_dir.exists():
            target_dir = self.data_dir / document_id
        return target_dir / "reviews.json"

    def _load_persisted_events(self):
        for document_id in self.list_ids():
            rf = self._reviews_file(document_id)
            if rf.is_file():
                try:
                    raw = json.loads(rf.read_text(encoding="utf-8"))
                    self._events[document_id] = [
                        ReviewEvent(
                            id=item["id"],
                            unit_id=item["unit_id"],
                            action=item["action"],
                            reviewer=item["reviewer"],
                            note=item.get("note"),
                            content_before=item.get("content_before"),
                            content_after=item.get("content_after"),
                            created_at=datetime.fromisoformat(item["created_at"]) if isinstance(item["created_at"], str) else item["created_at"],
                        )
                        for item in raw
                    ]
                except Exception:
                    pass

    def _persist_events(self, document_id: str):
        rf = self._reviews_file(document_id)
        events = self._events.get(document_id, [])
        data = [
            {
                "id": e.id,
                "unit_id": e.unit_id,
                "action": e.action,
                "reviewer": e.reviewer,
                "note": e.note,
                "content_before": e.content_before,
                "content_after": e.content_after,
                "created_at": e.created_at.isoformat() if hasattr(e.created_at, "isoformat") else str(e.created_at),
            }
            for e in events
        ]
        try:
            rf.parent.mkdir(parents=True, exist_ok=True)
            rf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

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
            progress_counts = None
            if self.can_review:
                reviews = fold_events(self._events.get(document_id, []))
                progress_counts = progress([u.unit_id for u in doc.legal_units], reviews)
            summaries.append(
                DocumentSummary(
                    document_id=document_id,
                    document_no=doc.document_no,
                    document_type=doc.document_type,
                    title=doc.title,
                    page_count=loaded.page_count,
                    unit_count=len(doc.legal_units),
                    progress=progress_counts,
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
        events = self._events.get(document_id, [])
        if events:
            known = {u.unit_id for u in loaded.doc.legal_units}
            valid_events = [e for e in events if e.unit_id in known]
            reviews = fold_events(valid_events)
            loaded.reviews = reviews
            loaded.events = valid_events
            loaded.doc.legal_units = apply_edits(loaded.doc.legal_units, reviews)
        loaded.can_review = self.can_review
        self._cache[document_id] = (stamp, loaded)
        return loaded

    def _record_review(
        self, document_id: str, unit_id: str, action: str, reviewer: str, note: str | None = None, content: str | None = None
    ) -> ReviewEvent:
        """Ghi một thao tác thẩm định trên tệp cục bộ."""
        if not self.can_review:
            raise ReviewProblem(["Thẩm định cần PostgreSQL (đặt POSTGRES_HOST hoặc DATABASE_URL)"])
        if not DOC_ID_RE.match(document_id):
            raise KeyError(document_id)
        loaded = self.get(document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise KeyError(unit_id)

        prior_events = loaded.events_of(unit_id)
        unit_review = loaded.reviews.get(unit_id)
        current_content = unit_review.edited_content if unit_review and unit_review.edited_content is not None else selected.content

        decision = decide(current_content, prior_events, action, reviewer, note=note, content=content)
        events = self._events.setdefault(document_id, [])
        new_id = len(events) + 1
        event = ReviewEvent(
            id=new_id,
            unit_id=unit_id,
            action=action,
            reviewer=decision["reviewer"],
            note=decision["note"],
            content_before=decision["content_before"],
            content_after=decision["content_after"],
            created_at=datetime.now(timezone.utc),
        )
        events.append(event)
        self._persist_events(document_id)
        self._cache.pop(document_id, None)
        return event

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

        return LoadedDocument(doc=doc, page_count=page_count, directory=directory, can_review=self.can_review)
