"""Mọi câu SQL của app nằm ở đây. Mỗi hàm nhận một kết nối psycopg (row_factory = dict_row)."""
from __future__ import annotations

from app.contracts import LegalDocument, LegalUnit

_UNIT_COLUMNS = (
    "unit_id, parent_unit_id, chapter, section, article, clause, point, title, content, "
    "effective_from, effective_to, status, is_confident, source_pages"
)


def find_hash(conn, document_id: str) -> str | None:
    row = conn.execute("SELECT content_hash FROM document WHERE document_id = %s", (document_id,)).fetchone()
    return row["content_hash"] if row else None


def delete_document(conn, document_id: str) -> None:
    """Xóa văn bản; các đơn vị xóa theo (ON DELETE CASCADE)."""
    conn.execute("DELETE FROM document WHERE document_id = %s", (document_id,))


def insert_document(conn, doc: LegalDocument, page_count: int, content_hash: str) -> int:
    from psycopg.types.json import Jsonb  # nhập tại đây để phần còn lại của app không cần psycopg khi chạy chế độ thư mục file

    row = conn.execute(
        "INSERT INTO document (document_id, document_no, document_type, title, issuer, issued_date, "
        "effective_from, effective_to, status, amended_by, replaced_by, page_count, content_hash) "
        "VALUES (%(document_id)s, %(document_no)s, %(document_type)s, %(title)s, %(issuer)s, %(issued_date)s, "
        "%(effective_from)s, %(effective_to)s, %(status)s, %(amended_by)s, %(replaced_by)s, %(page_count)s, "
        "%(content_hash)s) RETURNING id",
        {
            "document_id": doc.document_id,
            "document_no": doc.document_no,
            "document_type": doc.document_type,
            "title": doc.title,
            "issuer": doc.issuer,
            "issued_date": doc.issued_date,
            "effective_from": doc.effective_from,
            "effective_to": doc.effective_to,
            "status": doc.status,
            "amended_by": Jsonb(doc.amended_by),
            "replaced_by": doc.replaced_by,
            "page_count": page_count,
            "content_hash": content_hash,
        },
    ).fetchone()
    return row["id"]


def insert_units(conn, document_pk: int, units: list[LegalUnit]) -> None:
    rows = [
        {
            "document_pk": document_pk,
            "position": position,
            "unit_id": u.unit_id,
            "parent_unit_id": u.parent_unit_id,
            "chapter": u.chapter,
            "section": u.section,
            "article": u.article,
            "clause": u.clause,
            "point": u.point,
            "title": u.title,
            "content": u.content,
            "effective_from": u.effective_from,
            "effective_to": u.effective_to,
            "status": u.status,
            "is_confident": u.is_confident,
            "source_pages": list(u.source_pages),
        }
        for position, u in enumerate(units)
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO legal_unit (document_pk, position, unit_id, parent_unit_id, chapter, section, article, "
            "clause, point, title, content, effective_from, effective_to, status, is_confident, source_pages) "
            "VALUES (%(document_pk)s, %(position)s, %(unit_id)s, %(parent_unit_id)s, %(chapter)s, %(section)s, "
            "%(article)s, %(clause)s, %(point)s, %(title)s, %(content)s, %(effective_from)s, %(effective_to)s, "
            "%(status)s, %(is_confident)s, %(source_pages)s)",
            rows,
        )


def list_documents(conn) -> list[dict]:
    return conn.execute(
        "SELECT d.document_id, d.document_no, d.document_type, d.title, d.page_count, "
        "(SELECT count(*) FROM legal_unit u WHERE u.document_pk = d.id) AS unit_count "
        "FROM document d ORDER BY d.document_id"
    ).fetchall()


def get_document(conn, document_id: str) -> dict | None:
    return conn.execute(
        "SELECT id, document_id, document_no, document_type, title, issuer, issued_date, effective_from, "
        "effective_to, status, amended_by, replaced_by, page_count FROM document WHERE document_id = %s",
        (document_id,),
    ).fetchone()


def get_units(conn, document_pk: int) -> list[dict]:
    """Đơn vị của một văn bản, đúng thứ tự trong file nhập."""
    return conn.execute(
        f"SELECT {_UNIT_COLUMNS} FROM legal_unit WHERE document_pk = %s ORDER BY position", (document_pk,)
    ).fetchall()


# ---- thẩm định (bảng review_event, chỉ ghi thêm) ----

_EVENT_COLUMNS = "id, unit_id, action, reviewer, note, content_before, content_after, created_at"


def get_events(conn, document_id: str) -> list[dict]:
    """Mọi sự kiện thẩm định của một văn bản, theo thứ tự thời gian."""
    return conn.execute(
        f"SELECT {_EVENT_COLUMNS} FROM review_event WHERE document_id = %s ORDER BY id", (document_id,)
    ).fetchall()


def get_unit_events(conn, document_id: str, unit_id: str) -> list[dict]:
    return conn.execute(
        f"SELECT {_EVENT_COLUMNS} FROM review_event WHERE document_id = %s AND unit_id = %s ORDER BY id",
        (document_id, unit_id),
    ).fetchall()


def get_unit_content(conn, document_pk: int, unit_id: str) -> str | None:
    """Nội dung GỐC (chưa áp dụng bản sửa) của một đơn vị; None nếu không có đơn vị."""
    row = conn.execute(
        "SELECT content FROM legal_unit WHERE document_pk = %s AND unit_id = %s", (document_pk, unit_id)
    ).fetchone()
    return row["content"] if row else None


def lock_unit(conn, document_id: str, unit_id: str) -> None:
    """Hai người thao tác cùng một đơn vị cùng lúc sẽ xếp hàng (khóa mất khi giao dịch kết thúc)."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"review:{document_id}:{unit_id}",))


def insert_event(conn, document_id: str, unit_id: str, fields: dict) -> dict:
    return conn.execute(
        "INSERT INTO review_event (document_id, unit_id, action, reviewer, note, content_before, content_after) "
        "VALUES (%(document_id)s, %(unit_id)s, %(action)s, %(reviewer)s, %(note)s, %(content_before)s, %(content_after)s) "
        f"RETURNING {_EVENT_COLUMNS}",
        {"document_id": document_id, "unit_id": unit_id, **fields},
    ).fetchone()


def get_events_of_existing_units(conn) -> list[dict]:
    """Sự kiện của mọi văn bản, chỉ các đơn vị còn tồn tại (để đếm tiến độ ở màn hình danh sách)."""
    return conn.execute(
        "SELECT e.document_id, e.id, e.unit_id, e.action, e.reviewer, e.note, e.content_before, e.content_after, "
        "e.created_at FROM review_event e "
        "JOIN document d ON d.document_id = e.document_id "
        "JOIN legal_unit u ON u.document_pk = d.id AND u.unit_id = e.unit_id ORDER BY e.id"
    ).fetchall()
