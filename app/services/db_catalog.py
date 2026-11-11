"""Nguồn dữ liệu PostgreSQL. Cùng giao diện với DocumentStore: list_documents() và get(document_id).

Khác DocumentStore: có lưu thao tác thẩm định (record_review). `get` trả văn bản với nội dung ĐÃ áp dụng bản sửa chữ
và trạng thái thẩm định tính từ lịch sử (xem services/review.py).
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from app import repository
from app.contracts import LegalDocument

from .models import DOC_ID_RE, DocumentSummary, LoadedDocument
from .review import ReviewEvent, apply_edits, decide, fold_events, progress


def _event(row: dict) -> ReviewEvent:
    return ReviewEvent(
        id=row["id"],
        unit_id=row["unit_id"],
        action=row["action"],
        reviewer=row["reviewer"],
        note=row["note"],
        content_before=row["content_before"],
        content_after=row["content_after"],
        created_at=row["created_at"],
    )


class DbCatalog:
    def __init__(self, pool, storage_dir: Path):
        self.pool = pool
        self.storage_dir = Path(storage_dir)

    def list_documents(self) -> list[DocumentSummary]:
        with self.pool.connection() as conn:
            rows = repository.list_documents(conn)
            event_rows = repository.get_events_of_existing_units(conn)
        by_document = defaultdict(list)
        for row in event_rows:
            by_document[row["document_id"]].append(_event(row))
        summaries = []
        for row in rows:
            reviews = fold_events(by_document.get(row["document_id"], []))
            counts = progress(list(reviews), reviews)  # đếm trên các đơn vị đã có thao tác...
            counts["total"] = row["unit_count"]  # ...rồi lấy tổng thật; phần còn lại là chưa soát
            counts["unreviewed"] = row["unit_count"] - counts["approved"] - counts["flagged"] - counts["structure_error"]
            counts["percent_approved"] = round(100 * counts["approved"] / row["unit_count"]) if row["unit_count"] else 0
            summaries.append(DocumentSummary(**row, progress=counts))
        return summaries

    def get(self, document_id: str) -> LoadedDocument:
        """KeyError nếu không có văn bản."""
        if not DOC_ID_RE.match(document_id):
            raise KeyError(document_id)
        with self.pool.connection() as conn:
            row = repository.get_document(conn, document_id)
            if row is None:
                raise KeyError(document_id)
            units = repository.get_units(conn, row["id"])
            event_rows = repository.get_events(conn, document_id)
        doc = LegalDocument.model_validate(
            {
                "document_id": row["document_id"],
                "document_no": row["document_no"],
                "document_type": row["document_type"],
                "title": row["title"],
                "issuer": row["issuer"],
                "issued_date": row["issued_date"],
                "effective_from": row["effective_from"],
                "effective_to": row["effective_to"],
                "status": row["status"],
                "amended_by": row["amended_by"],
                "replaced_by": row["replaced_by"],
                "legal_units": units,
            }
        )
        known = {u.unit_id for u in doc.legal_units}
        events = [e for e in map(_event, event_rows) if e.unit_id in known]
        reviews = fold_events(events)
        doc.legal_units = apply_edits(doc.legal_units, reviews)
        return LoadedDocument(
            doc=doc,
            page_count=row["page_count"],
            directory=self.storage_dir / document_id,
            can_review=True,
            reviews=reviews,
            events=events,
        )

    def record_review(
        self, document_id: str, unit_id: str, action: str, reviewer: str, note: str | None = None, content: str | None = None
    ) -> ReviewEvent:
        """Ghi một thao tác thẩm định. KeyError nếu không có văn bản/đơn vị; ReviewProblem nếu thao tác không hợp lệ."""
        if not DOC_ID_RE.match(document_id):
            raise KeyError(document_id)
        with self.pool.connection() as conn:  # một giao dịch: khóa, đọc lịch sử, kiểm tra, ghi
            row = repository.get_document(conn, document_id)
            if row is None:
                raise KeyError(document_id)
            original = repository.get_unit_content(conn, row["id"], unit_id)
            if original is None:
                raise KeyError(unit_id)
            repository.lock_unit(conn, document_id, unit_id)
            prior = [_event(r) for r in repository.get_unit_events(conn, document_id, unit_id)]
            fields = decide(original, prior, action, reviewer, note, content)
            return _event(repository.insert_event(conn, document_id, unit_id, fields))

    def get_account(self, username: str) -> dict | None:
        with self.pool.connection() as conn:
            return repository.get_account(conn, username)

    def create_account(self, username: str, display_name: str, password_hash: str, role: str = "viewer", is_approved: bool = False) -> dict:
        with self.pool.connection() as conn:
            return repository.create_account(conn, username, display_name, password_hash, role, is_approved)

    def update_account_permission(self, username: str, role: str, is_approved: bool) -> dict | None:
        with self.pool.connection() as conn:
            return repository.update_account_permission(conn, username, role, is_approved)

    def update_account_password(self, username: str, password_hash: str) -> None:
        with self.pool.connection() as conn:
            repository.update_account_password(conn, username, password_hash)

    def upsert_account(self, username: str, display_name: str = "") -> dict:
        with self.pool.connection() as conn:
            return repository.upsert_account(conn, username, display_name or username)

    def list_accounts(self) -> list[dict]:
        with self.pool.connection() as conn:
            return repository.list_accounts(conn)

    def save_progress(self, username: str, document_id: str, unit_id: str, page: int) -> None:
        if not username or not username.strip():
            return
        with self.pool.connection() as conn:
            repository.save_reviewer_progress(conn, username, document_id, unit_id, page)

    def get_user_progress(self, username: str, document_id: str) -> dict | None:
        if not username or not username.strip():
            return None
        with self.pool.connection() as conn:
            return repository.get_reviewer_progress(conn, username, document_id)
