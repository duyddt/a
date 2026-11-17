"""Nguồn dữ liệu PostgreSQL. Cùng giao diện với DocumentStore: list_documents() và get(document_id).

Khác DocumentStore: có lưu thao tác thẩm định (record_review). `get` trả văn bản với nội dung ĐÃ áp dụng bản sửa chữ
và trạng thái thẩm định tính từ lịch sử (xem services/review.py), theo BẢN mà người xem được xem: người chỉnh sửa thấy
bản riêng của mình, người phê duyệt và Admin thấy bản chốt (hoặc bản của một người chỉnh sửa khi hỏi `version`).

PDF gốc nằm trong PostgreSQL (bảng document_pdf). `storage_dir/<document_id>/` chỉ là bộ đệm: `get` chép PDF ra
source.pdf khi bộ đệm thiếu hoặc cũ (so sha256 trong tệp .pdf_sha256), ảnh trang dựng khi có người xem (render_page).
Xóa bộ đệm lúc nào cũng được.
"""
from __future__ import annotations

import os
import uuid
from collections import defaultdict
from pathlib import Path

from app import repository
from app.contracts import LegalDocument

from .categories import Category
from .models import DOC_ID_RE, DocumentSummary, LoadedDocument
from .page_words import DbWordCache
from .review import (
    EDITOR,
    FINAL,
    ReviewEvent,
    ReviewProblem,
    apply_edits,
    decide,
    fold_events,
    fold_tracks,
    progress,
    track_of,
    version_for,
)
from .workflow import WorkflowEvent, can_act_problem, decide_workflow, fold_workflow, fold_workflows

PDF_MARKER = ".pdf_sha256"


def _workflow_event(row: dict) -> WorkflowEvent:
    return WorkflowEvent(**{k: row[k] for k in (
        "id", "document_id", "action", "actor", "target_user", "target_role", "note", "created_at")})


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
        track=row.get("track") or FINAL,
    )


class DbCatalog:
    def __init__(self, pool, storage_dir: Path):
        self.pool = pool
        self.storage_dir = Path(storage_dir)
        self.word_cache = DbWordCache(pool)

    def list_documents(self, viewer: dict | None = None) -> list[DocumentSummary]:
        """Tiến độ đếm trên bản của người xem: bản riêng của người chỉnh sửa, bản chốt với người phê duyệt / Admin."""
        mine = viewer["username"] if viewer and viewer.get("role") == "reviewer" else None
        with self.pool.connection() as conn:
            rows = repository.list_documents(conn)
            event_rows = repository.get_events_of_existing_units(conn)
            link_rows = repository.list_category_links(conn)
            workflows = fold_workflows(map(_workflow_event, repository.get_workflow_events(conn)))
        folders = defaultdict(dict)
        for link in link_rows:
            folders[link["document_id"]][link["code"]] = link["rank"]
        by_document = defaultdict(list)
        for row in event_rows:
            event = _event(row)
            if (event.track == EDITOR and event.reviewer == mine) if mine else event.track == FINAL:
                by_document[row["document_id"]].append(event)
        summaries = []
        for row in rows:
            reviews = fold_events(by_document.get(row["document_id"], []))
            counts = progress(list(reviews), reviews)  # đếm trên các đơn vị đã có thao tác...
            counts["total"] = row["unit_count"]  # ...rồi lấy tổng thật; phần còn lại là chưa soát
            counts["unreviewed"] = row["unit_count"] - counts["approved"] - counts["flagged"] - counts["structure_error"]
            counts["percent_approved"] = round(100 * counts["approved"] / row["unit_count"]) if row["unit_count"] else 0
            summaries.append(DocumentSummary(**row, progress=counts, categories=folders.get(row["document_id"], {}),
                                             workflow=workflows.get(row["document_id"]) or fold_workflow([], row["document_id"])))
        return summaries

    def list_categories(self) -> list[Category]:
        with self.pool.connection() as conn:
            return [Category(row["code"], row["name"], row["position"]) for row in repository.list_categories(conn)]

    def get(self, document_id: str, viewer: dict | None = None, version: str | None = None) -> LoadedDocument:
        """KeyError nếu không có văn bản. `viewer`: tài khoản đang xem (None = bản chốt); `version`: bản muốn xem."""
        if not DOC_ID_RE.match(document_id):
            raise KeyError(document_id)
        with self.pool.connection() as conn:
            row = repository.get_document(conn, document_id)
            if row is None:
                raise KeyError(document_id)
            units = repository.get_units(conn, row["id"])
            event_rows = repository.get_events(conn, document_id)
            workflow = fold_workflow(map(_workflow_event, repository.get_workflow_events(conn, document_id)), document_id)
        directory = self.storage_dir / document_id
        if row["pdf_sha256"]:  # văn bản nhập từ phiên bản cũ chưa có PDF trong DB: dùng tệp có sẵn trên đĩa
            self._cache_pdf(document_id, row["pdf_sha256"], directory)
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
        tracks = fold_tracks(e for e in map(_event, event_rows) if e.unit_id in known)
        shown = version_for(viewer, version, tracks.people)
        reviews = tracks.reviews_of(shown)
        original_units = doc.legal_units
        doc.legal_units = apply_edits(original_units, reviews)
        return LoadedDocument(
            doc=doc,
            page_count=row["page_count"],
            directory=directory,
            can_review=True,
            reviews=reviews,
            events=tracks.events_of(shown),
            pdf_sha256=row["pdf_sha256"],
            word_cache=self.word_cache,
            version=shown,
            tracks=tracks,
            original_units=original_units,
            workflow=workflow,
        )

    def _cache_pdf(self, document_id: str, sha256: str, directory: Path) -> None:
        """Bảo đảm directory/source.pdf đúng bản PDF đang lưu trong PostgreSQL. Ảnh trang của bản cũ tự bị bỏ nhờ .stamp."""
        marker = directory / PDF_MARKER
        try:
            if (directory / "source.pdf").is_file() and marker.read_text(encoding="utf-8") == sha256:
                return
        except OSError:
            pass  # chưa có tệp đánh dấu: chép mới
        with self.pool.connection() as conn:
            pdf = repository.get_pdf(conn, document_id)
        if pdf is None:  # vừa bị xóa hoặc thay giữa hai câu truy vấn
            return
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f".source-{uuid.uuid4().hex}.pdf"
        try:
            temporary.write_bytes(pdf)
            os.replace(temporary, directory / "source.pdf")  # thay nguyên khối: request khác không đọc phải tệp dở dang
        except OSError:
            temporary.unlink(missing_ok=True)  # Windows: tệp cũ đang mở để render; lần xem sau sẽ chép lại
            return
        marker.write_text(sha256, encoding="utf-8")

    def record_review(
        self, document_id: str, unit_id: str, action: str, reviewer: str, note: str | None = None,
        content: str | None = None, role: str = "admin", source: str | None = None,
    ) -> ReviewEvent:
        """Ghi một thao tác thẩm định vào bản của `role` (người chỉnh sửa: bản riêng; người phê duyệt / Admin: bản chốt).

        `source` (chỉ với "adopt"): bản được chọn, "ocr" hoặc tên một người chỉnh sửa; chữ của bản đó do server tự lấy.
        KeyError nếu không có văn bản/đơn vị; ReviewProblem nếu thao tác không hợp lệ."""
        if not DOC_ID_RE.match(document_id):
            raise KeyError(document_id)
        with self.pool.connection() as conn:  # một giao dịch: khóa, đọc lịch sử, kiểm tra, ghi
            row = repository.get_document(conn, document_id)
            if row is None:
                raise KeyError(document_id)
            original = repository.get_unit_content(conn, row["id"], unit_id)
            if original is None:
                raise KeyError(unit_id)
            # thao tác chỉ hợp lệ theo phân công: người chỉnh sửa được giao và chưa Hoàn thành, người phê duyệt khi văn
            # bản đã chuyển sang. Khóa chia sẻ: Hoàn thành / Trả lại không chen vào giữa lúc kiểm tra và lúc ghi.
            repository.lock_workflow(conn, document_id, shared=True)
            user = {"username": reviewer.strip(), "role": role}
            workflow = fold_workflow(map(_workflow_event, repository.get_workflow_events(conn, document_id)), document_id)
            blocked = can_act_problem(workflow, user)
            if blocked:
                raise ReviewProblem([blocked])
            repository.lock_unit(conn, document_id, unit_id)
            tracks = fold_tracks(_event(r) for r in repository.get_unit_events(conn, document_id, unit_id))
            mine = reviewer.strip() if track_of(role) == EDITOR else FINAL
            source_content = None
            if action == "adopt" and source:
                if source == "ocr":
                    source_content, label = original, "Chọn bản OCR gốc"
                elif source in tracks.editors:
                    state = tracks.editors[source].get(unit_id)
                    source_content = state.edited_content if state and state.edited_content is not None else original
                    label = f"Chọn bản của {source}"
                else:
                    raise ReviewProblem([f"Không có bản của {source} cho đơn vị này"])
                note = f"{label} · {note.strip()}" if note and note.strip() else label
            fields = decide(original, tracks.events_of(mine), action, reviewer, note, content,
                            role=role, source_content=source_content)
            return _event(repository.insert_event(conn, document_id, unit_id, fields))

    def stats(self, folder: str | None = None):
        """Thống kê tiến độ (stats.Stats) của mọi văn bản, hoặc chỉ văn bản trong thư mục `folder`."""
        from .stats import build_stats

        with self.pool.connection() as conn:
            rows = repository.list_documents(conn)
            event_rows = repository.get_events_of_existing_units(conn)
            link_rows = repository.list_category_links(conn)
            flow = [_workflow_event(r) for r in repository.get_workflow_events(conn)]
            accounts = repository.list_accounts(conn)
        folders = defaultdict(dict)
        for link in link_rows:
            folders[link["document_id"]][link["code"]] = link["rank"]
        documents = [DocumentSummary(**row, categories=folders.get(row["document_id"], {})) for row in rows]
        if folder:
            documents = [d for d in documents if folder in d.categories]
        events = defaultdict(list)
        for row in event_rows:
            events[row["document_id"]].append(_event(row))
        return build_stats(documents, events, fold_workflows(flow), accounts, flow)

    def workflow(self, document_id: str):
        """Trạng thái quy trình của một văn bản (workflow.Workflow). KeyError nếu không có văn bản."""
        if not DOC_ID_RE.match(document_id):
            raise KeyError(document_id)
        with self.pool.connection() as conn:
            if repository.get_document(conn, document_id) is None:
                raise KeyError(document_id)
            return fold_workflow(map(_workflow_event, repository.get_workflow_events(conn, document_id)), document_id)

    def workflows(self) -> dict:
        """document_id -> workflow.Workflow, chỉ các văn bản đã có sự kiện quy trình."""
        with self.pool.connection() as conn:
            return fold_workflows(map(_workflow_event, repository.get_workflow_events(conn)))

    def record_workflow(
        self, document_id: str, action: str, user: dict, note: str | None = None,
        target_user: str | None = None, target_role: str | None = None,
    ) -> WorkflowEvent:
        """Ghi một thao tác quy trình (phân công, Hoàn thành, Trả lại, Hoàn tất phê duyệt, mở lại).

        KeyError nếu không có văn bản; ReviewProblem nếu thao tác không hợp lệ với vai trò hoặc trạng thái hiện tại."""
        if not DOC_ID_RE.match(document_id):
            raise KeyError(document_id)
        with self.pool.connection() as conn:  # một giao dịch: khóa độc quyền, đọc lịch sử, kiểm tra, ghi
            row = repository.get_document(conn, document_id)
            if row is None:
                raise KeyError(document_id)
            repository.lock_workflow(conn, document_id)
            state = fold_workflow(map(_workflow_event, repository.get_workflow_events(conn, document_id)), document_id)
            account = repository.get_account(conn, target_user) if target_user and action == "assign" else None
            unapproved = 0
            if action == "finalize":
                unit_ids = repository.get_unit_ids(conn, row["id"])
                final = fold_events(e for e in map(_event, repository.get_events(conn, document_id))
                                    if e.track == FINAL and e.unit_id in set(unit_ids))
                unapproved = sum(1 for unit_id in unit_ids
                                 if final.get(unit_id) is None or final[unit_id].status != "approved")
            fields = decide_workflow(
                state, action, user, note, target_user, target_role,
                target_account_role=account["role"] if account else None, unapproved=unapproved,
            )
            return _workflow_event(repository.insert_workflow_event(conn, document_id, fields))

    def get_account(self, username: str) -> dict | None:
        with self.pool.connection() as conn:
            return repository.get_account(conn, username)

    def create_account(self, username: str, display_name: str, password_hash: str, role: str = "reviewer", is_approved: bool = True) -> dict:
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
