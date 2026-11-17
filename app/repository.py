"""Mọi câu SQL của app nằm ở đây. Mỗi hàm nhận một kết nối psycopg (row_factory = dict_row)."""
from __future__ import annotations

import hashlib

from app.contracts import LegalDocument, LegalUnit

_UNIT_COLUMNS = (
    "unit_id, parent_unit_id, chapter, section, article, clause, point, title, content, "
    "effective_from, effective_to, status, is_confident, source_pages"
)


def find_hash(conn, document_id: str) -> str | None:
    row = conn.execute("SELECT content_hash FROM document WHERE document_id = %s", (document_id,)).fetchone()
    return row["content_hash"] if row else None


def delete_document(conn, document_id: str) -> None:
    """Xóa văn bản; các đơn vị xóa theo (ON DELETE CASCADE), toạ độ chữ OCR của PDF không còn ai dùng cũng xóa."""
    conn.execute("DELETE FROM document WHERE document_id = %s", (document_id,))
    conn.execute("DELETE FROM page_words w WHERE NOT EXISTS (SELECT 1 FROM document_pdf p WHERE p.sha256 = w.pdf_sha256)")


def insert_document(conn, doc: LegalDocument, page_count: int, content_hash: str) -> int:
    from psycopg.types.json import (
        Jsonb,  # nhập tại đây để phần còn lại của app không cần psycopg khi chạy chế độ thư mục file
    )

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
    """Thông tin văn bản; `pdf_sha256` là None khi văn bản được nhập trước khi PDF lưu trong PostgreSQL."""
    return conn.execute(
        "SELECT d.id, d.document_id, d.document_no, d.document_type, d.title, d.issuer, d.issued_date, "
        "d.effective_from, d.effective_to, d.status, d.amended_by, d.replaced_by, d.page_count, p.sha256 AS pdf_sha256 "
        "FROM document d LEFT JOIN document_pdf p ON p.document_pk = d.id WHERE d.document_id = %s",
        (document_id,),
    ).fetchone()


# ---- PDF gốc (bảng document_pdf) ----


def has_pdf(conn, document_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM document_pdf p JOIN document d ON d.id = p.document_pk WHERE d.document_id = %s",
        (document_id,),
    ).fetchone() is not None


def store_pdf(conn, document_id: str, pdf_bytes: bytes) -> None:
    """Lưu (hoặc thay) PDF gốc của một văn bản đã có trong bảng document."""
    conn.execute(
        "INSERT INTO document_pdf (document_pk, sha256, pdf) SELECT id, %s, %s FROM document WHERE document_id = %s "
        "ON CONFLICT (document_pk) DO UPDATE SET sha256 = EXCLUDED.sha256, pdf = EXCLUDED.pdf",
        (hashlib.sha256(pdf_bytes).hexdigest(), pdf_bytes, document_id),
    )


def get_pdf(conn, document_id: str) -> bytes | None:
    row = conn.execute(
        "SELECT p.pdf FROM document_pdf p JOIN document d ON d.id = p.document_pk WHERE d.document_id = %s",
        (document_id,),
    ).fetchone()
    return bytes(row["pdf"]) if row else None


def get_pdf_with_sha(conn, document_id: str) -> tuple[bytes, str] | None:
    row = conn.execute(
        "SELECT p.pdf, p.sha256 FROM document_pdf p JOIN document d ON d.id = p.document_pk WHERE d.document_id = %s",
        (document_id,),
    ).fetchone()
    return (bytes(row["pdf"]), row["sha256"]) if row else None


def get_page_words(conn, pdf_sha256: str, page: int, engine: str) -> list | None:
    row = conn.execute(
        "SELECT words FROM page_words WHERE pdf_sha256 = %s AND page = %s AND engine = %s", (pdf_sha256, page, engine)
    ).fetchone()
    return row["words"] if row else None


def put_page_words(conn, pdf_sha256: str, page: int, engine: str, words: list) -> None:
    from psycopg.types.json import Jsonb

    conn.execute(
        "INSERT INTO page_words (pdf_sha256, page, engine, words) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (pdf_sha256, page, engine) DO UPDATE SET words = EXCLUDED.words, created_at = now()",
        (pdf_sha256, page, engine, Jsonb(words)),
    )


def pages_with_words(conn, pdf_sha256: str, engine: str) -> set[int]:
    rows = conn.execute("SELECT page FROM page_words WHERE pdf_sha256 = %s AND engine = %s", (pdf_sha256, engine))
    return {row["page"] for row in rows.fetchall()}


def find_document_by_pdf(conn, sha256: str) -> str | None:
    """Mã văn bản có PDF gốc trùng byte (theo sha256); None nếu chưa có."""
    row = conn.execute(
        "SELECT d.document_id FROM document_pdf p JOIN document d ON d.id = p.document_pk WHERE p.sha256 = %s "
        "ORDER BY d.document_id LIMIT 1",
        (sha256,),
    ).fetchone()
    return row["document_id"] if row else None


# ---- danh mục (bảng category, document_category) ----


def upsert_category(conn, code: str, name: str, position: int) -> None:
    """Tạo danh mục, hoặc cập nhật tên/thứ tự khi thư mục được đổi tên (mã số La Mã giữ nguyên)."""
    conn.execute(
        "INSERT INTO category (code, name, position) VALUES (%s, %s, %s) "
        "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, position = EXCLUDED.position",
        (code, name, position),
    )


def category_exists(conn, code: str) -> bool:
    return conn.execute("SELECT 1 FROM category WHERE code = %s", (code,)).fetchone() is not None


def link_category(conn, document_id: str, code: str, rank: int) -> None:
    """Xếp văn bản vào danh mục (đã có thì chỉ cập nhật thứ tự). Văn bản hoặc danh mục chưa có thì không làm gì."""
    conn.execute(
        "INSERT INTO document_category (category_id, document_pk, rank) "
        "SELECT c.id, d.id, %s FROM category c, document d WHERE c.code = %s AND d.document_id = %s "
        "ON CONFLICT (category_id, document_pk) DO UPDATE SET rank = EXCLUDED.rank",
        (rank, code, document_id),
    )


def get_category_links(conn, document_id: str) -> list[dict]:
    """[{code, rank}] của một văn bản, để giữ lại khi văn bản được nhập lại (replace xóa rồi tạo lại dòng document)."""
    return conn.execute(
        "SELECT c.code, dc.rank FROM document_category dc JOIN category c ON c.id = dc.category_id "
        "JOIN document d ON d.id = dc.document_pk WHERE d.document_id = %s ORDER BY c.position, c.code",
        (document_id,),
    ).fetchall()


def list_categories(conn) -> list[dict]:
    return conn.execute("SELECT code, name, position FROM category ORDER BY position, name").fetchall()


def list_category_links(conn) -> list[dict]:
    """Mọi cặp (văn bản, danh mục) kèm thứ tự trong danh mục."""
    return conn.execute(
        "SELECT d.document_id, c.code, dc.rank FROM document_category dc "
        "JOIN category c ON c.id = dc.category_id JOIN document d ON d.id = dc.document_pk "
        "ORDER BY c.position, c.code, dc.rank, d.document_id"
    ).fetchall()


def get_units(conn, document_pk: int) -> list[dict]:
    """Đơn vị của một văn bản, đúng thứ tự trong file nhập."""
    return conn.execute(
        f"SELECT {_UNIT_COLUMNS} FROM legal_unit WHERE document_pk = %s ORDER BY position", (document_pk,)
    ).fetchall()


# ---- thẩm định (bảng review_event, chỉ ghi thêm) ----

_EVENT_COLUMNS = "id, unit_id, action, reviewer, note, content_before, content_after, created_at, track"


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
        "INSERT INTO review_event (document_id, unit_id, action, reviewer, note, content_before, content_after, track) "
        "VALUES (%(document_id)s, %(unit_id)s, %(action)s, %(reviewer)s, %(note)s, %(content_before)s, %(content_after)s, "
        "%(track)s) "
        f"RETURNING {_EVENT_COLUMNS}",
        {"document_id": document_id, "unit_id": unit_id, **fields},
    ).fetchone()


def get_events_of_existing_units(conn) -> list[dict]:
    """Sự kiện của mọi văn bản, chỉ các đơn vị còn tồn tại (để đếm tiến độ ở màn hình danh sách)."""
    return conn.execute(
        "SELECT e.document_id, e.id, e.unit_id, e.action, e.reviewer, e.note, e.content_before, e.content_after, "
        "e.created_at, e.track FROM review_event e "
        "JOIN document d ON d.document_id = e.document_id "
        "JOIN legal_unit u ON u.document_pk = d.id AND u.unit_id = e.unit_id ORDER BY e.id"
    ).fetchall()


# ---- quy trình: phân công, hoàn thành, phê duyệt (bảng workflow_event, chỉ ghi thêm) ----

_WORKFLOW_COLUMNS = "id, document_id, action, actor, target_user, target_role, note, created_at"


def lock_workflow(conn, document_id: str, shared: bool = False) -> None:
    """Khóa quy trình của một văn bản tới hết giao dịch. Thao tác thẩm định giữ khóa chia sẻ (chạy song song với nhau);
    Hoàn thành / Trả lại / Hoàn tất giữ khóa độc quyền nên chờ các thao tác đang ghi xong, và ngược lại."""
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    conn.execute(f"SELECT {function}(hashtext(%s))", (f"workflow:{document_id}",))


def get_workflow_events(conn, document_id: str | None = None) -> list[dict]:
    """Sự kiện quy trình của một văn bản (hoặc mọi văn bản), theo thứ tự thời gian."""
    if document_id is None:
        return conn.execute(f"SELECT {_WORKFLOW_COLUMNS} FROM workflow_event ORDER BY id").fetchall()
    return conn.execute(
        f"SELECT {_WORKFLOW_COLUMNS} FROM workflow_event WHERE document_id = %s ORDER BY id", (document_id,)
    ).fetchall()


def insert_workflow_event(conn, document_id: str, fields: dict) -> dict:
    return conn.execute(
        "INSERT INTO workflow_event (document_id, action, actor, target_user, target_role, note) "
        "VALUES (%(document_id)s, %(action)s, %(actor)s, %(target_user)s, %(target_role)s, %(note)s) "
        f"RETURNING {_WORKFLOW_COLUMNS}",
        {"document_id": document_id, **fields},
    ).fetchone()


def get_unit_ids(conn, document_pk: int) -> list[str]:
    return [r["unit_id"] for r in conn.execute("SELECT unit_id FROM legal_unit WHERE document_pk = %s", (document_pk,))]


# ---- tài khoản người soát, phân quyền & tiến trình cá nhân ----

def get_account(conn, username: str) -> dict | None:
    return conn.execute(
        "SELECT username, display_name, password_hash, role, is_approved, created_at "
        "FROM reviewer_account WHERE username = %s",
        (username.strip(),),
    ).fetchone()


def create_account(conn, username: str, display_name: str, password_hash: str, role: str = "reviewer",
                   is_approved: bool = True) -> dict:
    return conn.execute(
        "INSERT INTO reviewer_account (username, display_name, password_hash, role, is_approved) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (username) DO NOTHING "  # không bao giờ ghi đè tài khoản đã có (mật khẩu, quyền)
        "RETURNING username, display_name, role, is_approved, created_at",
        (username.strip(), (display_name or username).strip(), password_hash, role, is_approved),
    ).fetchone()


def upsert_account(conn, username: str, display_name: str, password_hash: str = "") -> dict:
    return conn.execute(
        "INSERT INTO reviewer_account (username, display_name, password_hash) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (username) DO UPDATE SET display_name = EXCLUDED.display_name "
        "RETURNING username, display_name, role, is_approved, created_at",
        (username.strip(), (display_name or username).strip(), password_hash),
    ).fetchone()


def update_account_permission(conn, username: str, role: str, is_approved: bool) -> dict | None:
    return conn.execute(
        "UPDATE reviewer_account SET role = %s, is_approved = %s WHERE username = %s "
        "RETURNING username, display_name, role, is_approved, created_at",
        (role, is_approved, username.strip()),
    ).fetchone()


def update_account_password(conn, username: str, password_hash: str) -> None:
    conn.execute(
        "UPDATE reviewer_account SET password_hash = %s WHERE username = %s",
        (password_hash, username.strip()),
    )


def list_accounts(conn) -> list[dict]:
    return conn.execute(
        "SELECT username, display_name, role, is_approved, created_at "
        "FROM reviewer_account ORDER BY (role = 'admin') DESC, is_approved ASC, created_at DESC"
    ).fetchall()


def save_reviewer_progress(conn, username: str, document_id: str, unit_id: str, page: int) -> None:
    if not username or not username.strip():
        return
    conn.execute(
        "INSERT INTO reviewer_progress (username, document_id, last_unit_id, last_page, updated_at) "
        "VALUES (%s, %s, %s, %s, now()) "
        "ON CONFLICT (username, document_id) DO UPDATE SET "
        "last_unit_id = EXCLUDED.last_unit_id, last_page = EXCLUDED.last_page, updated_at = now()",
        (username.strip(), document_id, unit_id, page),
    )


def get_reviewer_progress(conn, username: str, document_id: str) -> dict | None:
    if not username or not username.strip():
        return None
    return conn.execute(
        "SELECT username, document_id, last_unit_id, last_page, updated_at "
        "FROM reviewer_progress WHERE username = %s AND document_id = %s",
        (username.strip(), document_id),
    ).fetchone()
