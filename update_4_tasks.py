# Lưu thành file: update_4_tasks.py tại D:\Mega\review-app-khung-chay-duoc\review-app
# Chạy bằng lệnh: python update_4_tasks.py
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ==============================================================================
# 1. MIGRATION 003: Bảng tài khoản & lưu tiến trình riêng từng người soát
#    (Cập nhật test_db_import.py để chấp nhận >= 2 migrations)
# ==============================================================================
MIGRATION_003 = """-- Tài khoản người soát và lưu vị trí tiến trình duyệt theo từng người
CREATE TABLE IF NOT EXISTS reviewer_account (
    username     text        PRIMARY KEY CHECK (length(btrim(username)) > 0),
    display_name text        NOT NULL,
    password_hash text       NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reviewer_progress (
    username     text        NOT NULL,
    document_id  text        NOT NULL,
    last_unit_id text        NOT NULL,
    last_page    integer     NOT NULL DEFAULT 1,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (username, document_id)
);
"""
(ROOT / "migrations" / "003_accounts.sql").write_text(MIGRATION_003, encoding="utf-8")

test_db_path = ROOT / "tests" / "test_db_import.py"
if test_db_path.is_file():
    tdb = test_db_path.read_text(encoding="utf-8")
    tdb = tdb.replace(
        "DROP TABLE IF EXISTS review_event, legal_unit, document, schema_migrations CASCADE",
        "DROP TABLE IF EXISTS reviewer_progress, reviewer_account, review_event, legal_unit, document, schema_migrations CASCADE",
    )
    tdb = tdb.replace(
        'assert count("schema_migrations") == 2',
        'assert count("schema_migrations") >= 2',
    )
    test_db_path.write_text(tdb, encoding="utf-8")


# ==============================================================================
# 2. CẬP NHẬT app/services/review.py: Thang đo khoa học CER, WER, UEM, SFI & Tiến trình theo tài khoản
# ==============================================================================
REVIEW_PY = '''"""Thẩm định: quy tắc thuần Python (không phụ thuộc FastAPI hay DB).

Bao gồm:
- Lịch sử sự kiện thẩm định append-only (fold_events, decide, apply_edits).
- Thang đo độ chính xác khoa học (CER/CAR, WER/WAR theo chuẩn ICDAR/UNLV ISRI, UEM, SFI và Composite Score).
- Quản lý tiến trình duyệt theo từng tài khoản (resume_unit_id).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Iterable

from app.contracts import LegalDocument, LegalUnit

ACTIONS = ("approve", "edit", "flag", "structure_error", "reset")
ACTION_LABELS = {
    "approve": "Duyệt",
    "edit": "Sửa chữ",
    "flag": "Gắn cờ",
    "structure_error": "Báo lỗi cấu trúc",
    "reset": "Bỏ đánh dấu",
}
STATUS_LABELS = {
    "unreviewed": "Chưa soát",
    "approved": "Đã duyệt",
    "flagged": "Gắn cờ",
    "structure_error": "Lỗi cấu trúc",
}
OCR_REASONS = (
    "Mờ/nhòe bản scan",
    "Sai dấu tiếng Việt",
    "Nhầm ký tự giống nhau (d/đ, 0/O, 1/l...)",
    "Mất chữ / dính chữ",
    "Lỗi công thức / ký hiệu / bảng biểu",
)
_STATUS_AFTER = {"approve": "approved", "flag": "flagged", "structure_error": "structure_error", "reset": "unreviewed"}
NOTE_REQUIRED = ("flag", "structure_error")
MAX_REVIEWER = 100
MAX_NOTE = 2000
MAX_CONTENT = 200_000


class ReviewProblem(Exception):
    """Thao tác không hợp lệ; `problems` là danh sách lỗi đọc được."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class ReviewEvent:
    id: int
    unit_id: str
    action: str
    reviewer: str
    note: str | None = None
    content_before: str | None = None
    content_after: str | None = None
    created_at: datetime | None = None

    @property
    def action_label(self) -> str:
        return ACTION_LABELS.get(self.action, self.action)


@dataclass
class UnitReview:
    status: str = "unreviewed"
    original_content: str | None = None
    edited_content: str | None = None
    edit_count: int = 0
    edit_notes: list[str] | None = None
    note: str | None = None
    last: ReviewEvent | None = None

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]


def normalize_text(text: str) -> str:
    """Trình duyệt gửi xuống dòng kiểu \\r\\n; chuẩn hóa về \\n để so sánh và lưu."""
    return text.replace("\\r\\n", "\\n").replace("\\r", "\\n")


def fold_events(events: Iterable[ReviewEvent]) -> dict[str, UnitReview]:
    """Tính trạng thái hiện tại của từng đơn vị. `events` phải theo thứ tự thời gian (id tăng dần)."""
    result: dict[str, UnitReview] = {}
    for event in events:
        state = result.setdefault(event.unit_id, UnitReview())
        state.last = event
        if event.action == "edit":
            if state.original_content is None and event.content_before is not None:
                state.original_content = event.content_before
            state.edited_content = event.content_after
            state.edit_count += 1
            if event.note:
                if state.edit_notes is None:
                    state.edit_notes = []
                state.edit_notes.append(event.note)
            if state.status == "approved":
                state.status = "unreviewed"
        elif event.action in _STATUS_AFTER:
            state.status = _STATUS_AFTER[event.action]
            state.note = event.note if event.action in NOTE_REQUIRED else None
    return result


def decide(
    original_content: str,
    prior_events: Iterable[ReviewEvent],
    action: str,
    reviewer: str,
    note: str | None = None,
    content: str | None = None,
) -> dict:
    """Kiểm tra một thao tác mới; trả các cột của sự kiện cần ghi. ReviewProblem nếu không hợp lệ."""
    problems: list[str] = []
    reviewer = (reviewer or "").strip()
    note = (note or "").strip() or None
    if action not in ACTIONS:
        problems.append(f"Thao tác không hợp lệ: {action!r} (chỉ nhận {', '.join(ACTIONS)})")
    if not reviewer:
        problems.append("Cần nhập tên người soát")
    elif len(reviewer) > MAX_REVIEWER:
        problems.append(f"Tên người soát quá dài (tối đa {MAX_REVIEWER} ký tự)")
    if note and len(note) > MAX_NOTE:
        problems.append(f"Ghi chú quá dài (tối đa {MAX_NOTE} ký tự)")
    if action in NOTE_REQUIRED and not note:
        problems.append(f"{ACTION_LABELS[action]} cần ghi chú nêu lý do")

    before = after = None
    if action == "edit":
        states = fold_events(prior_events)
        unit_state = next(iter(states.values()), UnitReview())
        current_text = unit_state.edited_content if unit_state.edited_content is not None else original_content
        after = normalize_text(content or "")
        if not after.strip():
            problems.append("Nội dung sửa không được để trống")
        elif len(after) > MAX_CONTENT:
            problems.append("Nội dung sửa quá dài")
        elif after == normalize_text(current_text):
            problems.append("Nội dung không thay đổi so với bản hiện hành")
        before = current_text

    if problems:
        raise ReviewProblem(problems)
    return {
        "action": action,
        "reviewer": reviewer,
        "note": note,
        "content_before": before,
        "content_after": after,
    }


def apply_edits(units: list[LegalUnit], reviews: dict[str, UnitReview]) -> list[LegalUnit]:
    """Bản sao các đơn vị với nội dung hiện hành (đã áp dụng bản sửa). Không đụng tới danh sách gốc."""
    out = []
    for unit in units:
        state = reviews.get(unit.unit_id)
        if state is not None and state.edited_content is not None:
            unit = unit.model_copy(update={"content": state.edited_content})
        out.append(unit)
    return out


def progress(unit_ids: Iterable[str], reviews: dict[str, UnitReview]) -> dict:
    """Đếm theo trạng thái trên các đơn vị hiện có (sự kiện của đơn vị không còn tồn tại bị bỏ qua)."""
    counts = {"approved": 0, "flagged": 0, "structure_error": 0, "unreviewed": 0}
    total = 0
    for unit_id in unit_ids:
        total += 1
        state = reviews.get(unit_id)
        counts[state.status if state else "unreviewed"] += 1
    percent = round(100 * counts["approved"] / total) if total else 0
    return {"total": total, **counts, "percent_approved": percent}


def resume_unit_id(
    unit_ids: list[str],
    reviews: dict[str, UnitReview],
    events: Iterable[ReviewEvent] = (),
    reviewer: str | None = None,
    saved_unit_id: str | None = None,
) -> str | None:
    """Tìm đơn vị cần nhảy tới khi mở lại văn bản (ưu tiên tiến trình của tài khoản `reviewer` hiện tại):
    1. Nếu tài khoản có `saved_unit_id` (lưu trong bảng tiến trình cá nhân) và đơn vị đó đang sửa dở -> mở ngay.
    2. Tìm sự kiện mới nhất của chính `reviewer` đó (nếu chưa có thì xét sự kiện chung).
    3. Nếu đơn vị vừa thao tác vẫn 'unreviewed' -> giữ nguyên; nếu đã duyệt xong -> nhảy tới đơn vị 'unreviewed' kế tiếp.
    """
    if not unit_ids:
        return None
    known = set(unit_ids)

    if saved_unit_id and saved_unit_id in known:
        st = reviews.get(saved_unit_id)
        if st is None or st.status == "unreviewed":
            return saved_unit_id

    all_events = [ev for ev in events if ev.unit_id in known]
    user_events = [
        ev for ev in all_events
        if reviewer and ev.reviewer.strip().lower() == reviewer.strip().lower()
    ]
    target_events = user_events if user_events else all_events

    last_unit = target_events[-1].unit_id if target_events else (saved_unit_id if saved_unit_id in known else None)

    if last_unit:
        last_state = reviews.get(last_unit)
        if last_state and last_state.status == "unreviewed":
            return last_unit
        start_idx = (unit_ids.index(last_unit) + 1) % len(unit_ids)
        ordered = unit_ids[start_idx:] + unit_ids[:start_idx]
    else:
        ordered = unit_ids

    for uid in ordered:
        st = reviews.get(uid)
        if st is None or st.status == "unreviewed":
            return uid
    return last_unit or unit_ids[0]


def _edit_distance_seq(seq_a: list[str] | str, seq_b: list[str] | str) -> int:
    """Tính khoảng cách chỉnh sửa (Số thao tác Insert + Delete + Substitute) từ opcodes của SequenceMatcher."""
    matcher = SequenceMatcher(None, seq_a, seq_b)
    edits = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            edits += max(i2 - i1, j2 - j1)
        elif tag == "delete":
            edits += i2 - i1
        elif tag == "insert":
            edits += j2 - j1
    return edits


def extract_char_diffs(before: str, after: str) -> list[dict]:
    """So sánh bản OCR gốc và bản đã sửa để lấy danh sách các cụm ký tự/từ bị OCR đọc sai."""
    before_norm = normalize_text(before)
    after_norm = normalize_text(after)
    matcher = SequenceMatcher(None, before_norm, after_norm)
    diffs: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        wrong = before_norm[i1:i2]
        correct = after_norm[j1:j2]
        if len(wrong) <= 40 and len(correct) <= 40:
            diffs.append({"type": tag, "ocr_read": wrong, "corrected_to": correct})
    return diffs


def build_inline_diff_html(before: str, after: str) -> str:
    """Tạo chuỗi HTML tô đỏ chỗ OCR đọc sai (<del>) và tô xanh chỗ đã sửa (<ins>) để đối chiếu trực quan."""
    import html
    before_norm = normalize_text(before)
    after_norm = normalize_text(after)
    matcher = SequenceMatcher(None, before_norm, after_norm)
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        b_esc = html.escape(before_norm[i1:i2])
        a_esc = html.escape(after_norm[j1:j2])
        if tag == "equal":
            parts.append(b_esc)
        elif tag == "delete":
            parts.append(f'<del class="diff-del" title="OCR đọc dư/sai">{b_esc}</del>')
        elif tag == "insert":
            parts.append(f'<ins class="diff-ins" title="Chữ đúng thêm vào">{a_esc}</ins>')
        elif tag == "replace":
            parts.append(f'<del class="diff-del" title="OCR đọc sai">{b_esc}</del><ins class="diff-ins" title="Đã sửa lại">{a_esc}</ins>')
    return "".join(parts)


def _suggest_solution(ocr_read: str, corrected_to: str, reason: str | None) -> str:
    reason_lower = (reason or "").lower()
    if "mờ" in reason_lower or "nhòe" in reason_lower or "scan" in reason_lower:
        return "Tiền xử lý ảnh ở 300 DPI, khử nhiễu Gaussian và nhị phân hóa thích nghi (Adaptive Thresholding)."
    if (ocr_read.lower() == "d" and corrected_to.lower() == "đ") or ("d" in ocr_read and "đ" in corrected_to):
        return "Áp dụng luật thứ tự Điểm pháp lý Việt Nam (a, b, c, d, đ, e, g) và từ điển hậu xử lý."
    if any(c in ocr_read + corrected_to for c in ("0", "O", "1", "l", "I", "5", "S")):
        return "Ràng buộc ngữ cảnh số/chữ bằng Regex cho số hiệu văn bản, ngày tháng và Khoản/Điểm."
    if not ocr_read.strip() and corrected_to.strip():
        return "Mở rộng vùng cắt dòng (Line Segmentation Padding) để không mất ký tự sát lề."
    if ocr_read.strip() and not corrected_to.strip():
        return "Lọc kênh màu đỏ (HSV Masking) để loại bỏ con dấu mộc đè lên chữ."
    return "Hậu xử lý bằng mô hình ngôn ngữ (Vietnamese Legal LM) và từ điển Thuế - Kế toán."


def build_ocr_report(doc: LegalDocument, reviews: dict[str, UnitReview]) -> dict:
    """Tính bộ thang đo độ chính xác khoa học (CER/CAR, WER/WAR, UEM, SFI, Composite Score) và thống kê lỗi."""
    approved_units = 0
    reviewed_units = 0
    structure_error_units = 0
    exact_match_units = 0
    edited_units_count = 0

    total_gt_chars = 0
    total_char_edits = 0
    total_gt_words = 0
    total_word_edits = 0

    pair_counter: Counter[tuple[str, str]] = Counter()
    reason_counter: Counter[str] = Counter()
    unit_errors: list[dict] = []

    for unit in doc.legal_units:
        state = reviews.get(unit.unit_id)
        current_text = normalize_text(unit.content)
        orig_text = normalize_text(state.original_content) if (state and state.original_content is not None) else current_text

        if state and state.status != "unreviewed":
            reviewed_units += 1
            if state.status == "structure_error":
                structure_error_units += 1

        if state and state.edit_count > 0:
            edited_units_count += 1
            diffs = extract_char_diffs(orig_text, current_text)
            reasons = state.edit_notes or ["Chưa ghi rõ nguyên nhân"]
            for r in reasons:
                reason_counter[r] += 1
            for d in diffs:
                pair_counter[(d["ocr_read"], d["corrected_to"])] += 1
            unit_errors.append({
                "unit_id": unit.unit_id,
                "pages": unit.source_pages,
                "edit_count": state.edit_count,
                "reasons": reasons,
                "diffs": diffs[:10],
                "diff_html": build_inline_diff_html(orig_text, current_text),
                "solution": _suggest_solution(
                    diffs[0]["ocr_read"] if diffs else "",
                    diffs[0]["corrected_to"] if diffs else "",
                    reasons[-1] if reasons else None,
                ),
            })

        if state and state.status == "approved":
            approved_units += 1
            gt_chars = max(len(current_text), 1)
            gt_words_list = current_text.split()
            gt_words = max(len(gt_words_list), 1)
            total_gt_chars += gt_chars
            total_gt_words += gt_words

            if state.edit_count == 0 or orig_text == current_text:
                exact_match_units += 1
            else:
                total_char_edits += _edit_distance_seq(orig_text, current_text)
                total_word_edits += _edit_distance_seq(orig_text.split(), gt_words_list)

    cer = round(100.0 * total_char_edits / total_gt_chars, 2) if total_gt_chars > 0 else None
    car = round(max(0.0, 100.0 - cer), 2) if cer is not None else None
    wer = round(100.0 * total_word_edits / total_gt_words, 2) if total_gt_words > 0 else None
    war = round(max(0.0, 100.0 - wer), 2) if wer is not None else None
    uem = round(100.0 * exact_match_units / approved_units, 2) if approved_units > 0 else None
    sfi = round(100.0 * (1.0 - structure_error_units / reviewed_units), 2) if reviewed_units > 0 else None

    composite = None
    if car is not None and war is not None and uem is not None and sfi is not None:
        composite = round(0.4 * car + 0.3 * war + 0.2 * sfi + 0.1 * uem, 2)

    common_substitutions = [
        {
            "ocr_read": k[0],
            "corrected_to": k[1],
            "count": count,
            "solution": _suggest_solution(k[0], k[1], None),
        }
        for k, count in pair_counter.most_common(20)
    ]

    return {
        "approved_units": approved_units,
        "reviewed_units": reviewed_units,
        "total_units": len(doc.legal_units),
        "edited_units": edited_units_count,
        "exact_match_units": exact_match_units,
        "cer_percent": cer,
        "char_accuracy_percent": car,
        "wer_percent": wer,
        "word_accuracy_percent": war,
        "unit_accuracy_percent": uem,
        "structural_fidelity_percent": sfi,
        "composite_score": composite,
        "reasons_summary": [{"reason": r, "count": c} for r, c in reason_counter.most_common()],
        "common_substitutions": common_substitutions,
        "unit_errors": unit_errors,
    }


def build_export(doc: LegalDocument, reviews: dict[str, UnitReview]) -> tuple[dict, dict]:
    """JSON các đơn vị ĐÃ DUYỆT (nội dung hiện hành, kể cả bản sửa), cùng các trường như LegalDocument."""
    approved = [u for u in doc.legal_units if reviews.get(u.unit_id, UnitReview()).status == "approved"]
    approved_ids = {u.unit_id for u in approved}
    data = doc.model_dump(mode="json", exclude={"legal_units"})
    data["legal_units"] = [u.model_dump(mode="json") for u in approved]
    orphans = sorted(u.unit_id for u in approved if u.parent_unit_id and u.parent_unit_id not in approved_ids)
    stats = {
        "total": len(doc.legal_units),
        "exported": len(approved),
        "parent_not_exported": orphans,
    }
    return data, stats
'''
(ROOT / "app" / "services" / "review.py").write_text(REVIEW_PY, encoding="utf-8")


# ==============================================================================
# 3. CẬP NHẬT app/repository.py & app/services/db_catalog.py & store.py: Lưu tài khoản & tiến trình
# ==============================================================================
repo_path = ROOT / "app" / "repository.py"
repo_code = repo_path.read_text(encoding="utf-8")
ACCOUNT_SQL = '''

# ---- tài khoản người soát & tiến trình cá nhân ----

def upsert_account(conn, username: str, display_name: str, password_hash: str = "") -> dict:
    return conn.execute(
        "INSERT INTO reviewer_account (username, display_name, password_hash) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (username) DO UPDATE SET display_name = EXCLUDED.display_name "
        "RETURNING username, display_name, created_at",
        (username.strip(), (display_name or username).strip(), password_hash),
    ).fetchone()


def list_accounts(conn) -> list[dict]:
    return conn.execute(
        "SELECT username, display_name, created_at FROM reviewer_account ORDER BY created_at DESC"
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


def list_reviewer_progress(conn, username: str) -> list[dict]:
    if not username or not username.strip():
        return []
    return conn.execute(
        "SELECT document_id, last_unit_id, last_page, updated_at FROM reviewer_progress WHERE username = %s",
        (username.strip(),),
    ).fetchall()
'''
if "def upsert_account" not in repo_code:
    repo_path.write_text(repo_code + ACCOUNT_SQL, encoding="utf-8")

db_cat_path = ROOT / "app" / "services" / "db_catalog.py"
db_cat_code = db_cat_path.read_text(encoding="utf-8")
DB_CAT_EXTRA = '''
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
            repository.upsert_account(conn, username, username)
            repository.save_reviewer_progress(conn, username, document_id, unit_id, page)

    def get_user_progress(self, username: str, document_id: str) -> dict | None:
        if not username or not username.strip():
            return None
        with self.pool.connection() as conn:
            return repository.get_reviewer_progress(conn, username, document_id)
'''
if "def upsert_account" not in db_cat_code:
    db_cat_path.write_text(db_cat_code + DB_CAT_EXTRA, encoding="utf-8")


# ==============================================================================
# 4. CẬP NHẬT app/main.py: Hỗ trợ đăng nhập/tạo tài khoản, lưu tiến trình tự động & chế độ sửa 1 mục
# ==============================================================================
MAIN_PY = '''"""FastAPI: API JSON (/api, xem /docs) và giao diện kiểu File Explorer (Jinja2 + HTMX)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import get_catalog, load_or_404
from app.api import router as api_router
from app.config import Settings, load_settings
from app.services.models import ImportProblem
from app.services.review import (
    ACTION_LABELS,
    OCR_REASONS,
    ReviewProblem,
    build_inline_diff_html,
    build_ocr_report,
    extract_char_diffs,
    progress,
    resume_unit_id,
)
from app.services.store import DocumentStore
from app.services.tree import build_tree, decorate_for_page
from app.services.views import DEFAULT_ZOOM, first_page_of, nearest_zoom, viewer_context, vn_time

BASE_DIR = Path(__file__).resolve().parent
log = logging.getLogger("review-app")
REVIEWER_COOKIE = "reviewer"

# Bộ nhớ tạm cho chế độ thư mục file (khi chưa bật PostgreSQL vẫn thử được tài khoản & tiến trình)
_FILE_MODE_ACCOUNTS: dict[str, dict] = {}
_FILE_MODE_PROGRESS: dict[tuple[str, str], dict] = {}


def progress_of(loaded) -> dict | None:
    if not loaded.can_review:
        return None
    p = progress([u.unit_id for u in loaded.doc.legal_units], loaded.reviews)
    report = build_ocr_report(loaded.doc, loaded.reviews)
    p["char_accuracy_percent"] = report["char_accuracy_percent"]
    p["word_accuracy_percent"] = report["word_accuracy_percent"]
    p["unit_accuracy_percent"] = report["unit_accuracy_percent"]
    p["structural_fidelity_percent"] = report["structural_fidelity_percent"]
    p["composite_score"] = report["composite_score"]
    p["cer_percent"] = report["cer_percent"]
    p["wer_percent"] = report["wer_percent"]
    p["edited_units"] = report["edited_units"]
    p["ocr_report"] = report
    return p


def _save_user_pos(catalog, username: str, document_id: str, unit_id: str | None, page: int) -> None:
    username = (username or "").strip()
    if not username or not unit_id:
        return
    if hasattr(catalog, "save_progress"):
        try:
            catalog.save_progress(username, document_id, unit_id, page)
            return
        except Exception:
            pass
    _FILE_MODE_ACCOUNTS.setdefault(username, {"username": username, "display_name": username})
    _FILE_MODE_PROGRESS[(username, document_id)] = {
        "username": username,
        "document_id": document_id,
        "last_unit_id": unit_id,
        "last_page": page,
    }


def _get_user_pos(catalog, username: str, document_id: str) -> dict | None:
    username = (username or "").strip()
    if not username:
        return None
    if hasattr(catalog, "get_user_progress"):
        try:
            res = catalog.get_user_progress(username, document_id)
            if res:
                return res
        except Exception:
            pass
    return _FILE_MODE_PROGRESS.get((username, document_id))


def _list_accounts(catalog) -> list[dict]:
    if hasattr(catalog, "list_accounts"):
        try:
            return catalog.list_accounts()
        except Exception:
            pass
    return list(_FILE_MODE_ACCOUNTS.values())


def create_app(settings: Settings | None = None, catalog=None) -> FastAPI:
    settings = settings or load_settings()
    use_db = catalog is None and settings.conninfo is not None
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    templates.env.filters["vn_time"] = vn_time
    templates.env.globals["ACTION_LABELS"] = ACTION_LABELS
    templates.env.globals["OCR_REASONS"] = OCR_REASONS

    def detail_context(request: Request, loaded, unit, **extra) -> dict:
        unit_review = loaded.reviews.get(unit.unit_id) if unit else None
        inline_diff = None
        char_diffs = []
        if unit and unit_review and unit_review.edit_count > 0 and unit_review.original_content is not None:
            inline_diff = build_inline_diff_html(unit_review.original_content, unit.content)
            char_diffs = extract_char_diffs(unit_review.original_content, unit.content)
        current_user = unquote(request.cookies.get(REVIEWER_COOKIE, "")).strip()
        context = {
            "doc": loaded.doc,
            "unit": unit,
            "inline_diff": inline_diff,
            "char_diffs": char_diffs,
            "can_review": loaded.can_review,
            "review": unit_review,
            "history": list(reversed(loaded.events_of(unit.unit_id))) if unit else [],
            "reviewer": current_user,
            "accounts": _list_accounts(request.app.state.catalog),
            "errors": [],
            "form_note": "",
            "form_content": None,
        }
        context.update(extra)
        return context

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = None
        if use_db:
            from app.db import create_pool, migrate
            from app.services.db_catalog import DbCatalog
            from app.services.importer import Importer

            pool = create_pool(settings.conninfo)
            for name in migrate(pool):
                log.info("Đã áp dụng migration %s", name)
            app.state.catalog = DbCatalog(pool, settings.storage_dir)
            app.state.importer = Importer(pool, settings.storage_dir)
            if settings.auto_import_dir:
                for name, outcome in app.state.importer.import_all(settings.auto_import_dir):
                    log.info("Nhập tự động %s: %s", name, outcome)
        try:
            yield
        finally:
            if pool is not None:
                pool.close()

    app = FastAPI(title="Web app thẩm định dữ liệu pháp lý", lifespan=lifespan)
    app.state.catalog = catalog or (None if use_db else DocumentStore(settings.data_dir))
    app.state.importer = None
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.include_router(api_router)

    @app.exception_handler(ReviewProblem)
    async def review_problem_handler(request: Request, exc: ReviewProblem):
        return JSONResponse(status_code=422, content={"detail": "Thao tác không hợp lệ", "problems": exc.problems})

    @app.exception_handler(ImportProblem)
    async def import_problem_handler(request: Request, exc: ImportProblem):
        if request.url.path.startswith("/api"):
            return JSONResponse(
                status_code=422,
                content={"detail": f"Không nhập được {exc.document_id}", "problems": exc.problems, "truncated": exc.truncated},
            )
        return templates.TemplateResponse(request, "import_error.html", {"problem": exc}, status_code=422)

    @app.post("/account/login", include_in_schema=False)
    def login_account(
        request: Request,
        username: str = Form(...),
        display_name: str = Form(""),
        redirect_to: str = Form("/"),
        catalog=Depends(get_catalog),
    ):
        """Tạo hoặc chuyển tài khoản người soát và lưu vào cookie để theo dõi tiến trình riêng."""
        clean_user = username.strip()[:100]
        if clean_user:
            if hasattr(catalog, "upsert_account"):
                try:
                    catalog.upsert_account(clean_user, (display_name or clean_user).strip()[:100])
                except Exception:
                    pass
            _FILE_MODE_ACCOUNTS[clean_user] = {
                "username": clean_user,
                "display_name": (display_name or clean_user).strip()[:100],
            }
        resp = RedirectResponse(url=redirect_to or "/", status_code=303)
        if clean_user:
            resp.set_cookie(REVIEWER_COOKIE, quote(clean_user, safe=""), max_age=365 * 24 * 3600, samesite="lax")
        else:
            resp.delete_cookie(REVIEWER_COOKIE)
        return resp

    @app.get("/", include_in_schema=False)
    def document_list(request: Request, catalog=Depends(get_catalog)):
        current_user = unquote(request.cookies.get(REVIEWER_COOKIE, "")).strip()
        items = catalog.list_documents()
        user_progress_map = {}
        for item in items:
            if not item.problem and current_user:
                up = _get_user_pos(catalog, current_user, item.document_id)
                if up:
                    user_progress_map[item.document_id] = up
        return templates.TemplateResponse(
            request,
            "list.html",
            {
                "items": items,
                "reviewer": current_user,
                "accounts": _list_accounts(catalog),
                "user_progress_map": user_progress_map,
            },
        )

    @app.get("/documents/{document_id}", include_in_schema=False)
    def review(request: Request, document_id: str, unit: str | None = None, page: int | None = None,
               zoom: int = DEFAULT_ZOOM, catalog=Depends(get_catalog)):
        loaded = load_or_404(catalog, document_id)
        units = loaded.units_by_id
        current_user = unquote(request.cookies.get(REVIEWER_COOKIE, "")).strip()
        saved_pos = _get_user_pos(catalog, current_user, document_id)
        saved_unit = saved_pos["last_unit_id"] if saved_pos else None

        if unit is None and loaded.doc.legal_units:
            unit = resume_unit_id(
                [u.unit_id for u in loaded.doc.legal_units],
                loaded.reviews,
                loaded.events,
                reviewer=current_user,
                saved_unit_id=saved_unit,
            )
        selected = units.get(unit) if unit else None
        if unit and selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        zoom = nearest_zoom(zoom)
        target_page = page or first_page_of(selected)
        if current_user and selected:
            _save_user_pos(catalog, current_user, document_id, selected.unit_id, target_page)

        tree = build_tree(loaded.doc.legal_units, loaded.reviews)
        decorate_for_page(tree, document_id, unit, zoom)
        context = detail_context(
            request, loaded, selected,
            tree=tree,
            v=viewer_context(loaded, target_page, zoom, unit),
            progress=progress_of(loaded),
            saved_pos=saved_pos,
            oob=False,
        )
        return templates.TemplateResponse(request, "review.html", context)

    @app.get("/documents/{document_id}/pages/{page}", include_in_schema=False)
    def page_image(document_id: str, page: int, catalog=Depends(get_catalog)):
        loaded = load_or_404(catalog, document_id)
        if page < 1 or page > loaded.page_count:
            raise HTTPException(status_code=404, detail="Không có trang này")
        try:
            image = loaded.page_image(page)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Không render được trang {page}: {exc}")
        return FileResponse(image, media_type="image/png")

    @app.get("/documents/{document_id}/viewer", include_in_schema=False)
    def viewer(request: Request, document_id: str, page: int = 1, zoom: int = DEFAULT_ZOOM,
               unit: str | None = None, catalog=Depends(get_catalog)):
        loaded = load_or_404(catalog, document_id)
        context = {"v": viewer_context(loaded, page, zoom, unit), "oob": False}
        return templates.TemplateResponse(request, "partials/viewer.html", context)

    @app.get("/documents/{document_id}/units/{unit_id}", include_in_schema=False)
    def unit_detail(request: Request, document_id: str, unit_id: str, zoom: int = DEFAULT_ZOOM,
                    catalog=Depends(get_catalog)):
        loaded = load_or_404(catalog, document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        current_user = unquote(request.cookies.get(REVIEWER_COOKIE, "")).strip()
        target_page = first_page_of(selected)
        if current_user:
            _save_user_pos(catalog, current_user, document_id, unit_id, target_page)
        context = detail_context(
            request, loaded, selected, v=viewer_context(loaded, target_page, nearest_zoom(zoom), unit_id), oob=True
        )
        return templates.TemplateResponse(request, "unit_response.html", context)

    @app.post("/documents/{document_id}/units/{unit_id}/review", include_in_schema=False)
    def review_action(request: Request, document_id: str, unit_id: str, action: str = Form(...), reviewer: str = Form(""),
                      note: str = Form(""), ocr_reason: str = Form(""), content: str = Form(""),
                      catalog=Depends(get_catalog)):
        loaded = load_or_404(catalog, document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        combined_note = note.strip()
        if action == "edit" and ocr_reason.strip():
            combined_note = f"[{ocr_reason.strip()}] {combined_note}".strip()
        errors: list[str] = []
        if not hasattr(catalog, "record_review"):
            errors = ["Thẩm định cần PostgreSQL (đặt POSTGRES_HOST hoặc DATABASE_URL)"]
        else:
            try:
                catalog.record_review(document_id, unit_id, action, reviewer, combined_note or None, content)
            except ReviewProblem as exc:
                errors = exc.problems
        if errors:
            context = detail_context(request, loaded, selected, errors=errors, form_note=note,
                                     form_content=content if action == "edit" else None, reviewer=reviewer, oob=False)
            return templates.TemplateResponse(request, "partials/detail.html", context)
        loaded = catalog.get(document_id)
        selected = loaded.units_by_id[unit_id]
        clean_reviewer = reviewer.strip()
        # Lưu tiến trình tiếp theo cho tài khoản vừa thao tác
        next_uid = resume_unit_id(
            [u.unit_id for u in loaded.doc.legal_units],
            loaded.reviews,
            loaded.events,
            reviewer=clean_reviewer,
            saved_unit_id=unit_id,
        ) or unit_id
        _save_user_pos(catalog, clean_reviewer, document_id, next_uid, first_page_of(loaded.units_by_id.get(next_uid)))

        context = detail_context(request, loaded, selected, reviewer=clean_reviewer, progress=progress_of(loaded))
        response = templates.TemplateResponse(request, "review_response.html", context)
        response.set_cookie(REVIEWER_COOKIE, quote(clean_reviewer, safe=""), max_age=365 * 24 * 3600, samesite="lax")
        return response

    return app


app = create_app()
'''
(ROOT / "app" / "main.py").write_text(MAIN_PY, encoding="utf-8")


# ==============================================================================
# 5. GIAO DIỆN FILE EXPLORER (app/static/style.css) + CHẾ ĐỘ SỬA 1 MỤC (ẨN CÂY)
# ==============================================================================
STYLE_CSS = '''
:root {
  --border: #cbd5e1;
  --bg-explorer: #f8fafc;
  --bg-ribbon: #f1f5f9;
  --muted: #475569;
  --accent: #0284c7;
  --accent-selected: #cce8ff;
  --accent-border: #0078d4;
  --warn: #b45309;
  --err: #b91c1c;
  --compare-font-size: 18px;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "Segoe UI", Tahoma, system-ui, -apple-system, sans-serif;
  font-size: 15px;
  color: #0f172a;
  background: #e2e8f0;
}
a { color: var(--accent-border); text-decoration: none; font-weight: 600; }
a:hover { text-decoration: underline; }
button { font: inherit; cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .5; }
code { background: #e2e8f0; padding: 2px 6px; border-radius: 3px; font-size: 0.92em; font-weight: 600; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.err { color: var(--err); }
.narrow { max-width: 1240px; margin: 0 auto; padding: 20px 16px; }

/* Thanh tiêu đề cửa sổ kiểu Windows File Explorer */
.topbar {
  display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px;
  padding: 8px 16px; background: #ffffff; border-bottom: 1px solid var(--border);
}
.topbar h1 { margin: 0; font-size: 16px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
.topbar .button-link {
  padding: 6px 12px; border: 1px solid #94a3b8; background: #f8fafc;
  border-radius: 4px; color: #0f172a; font-weight: 600; font-size: 13px;
}
.topbar .button-link:hover { background: #e2e8f0; text-decoration: none; }

/* Khung đăng nhập / chuyển tài khoản nhanh trên thanh Explorer */
.account-bar {
  display: flex; align-items: center; gap: 6px; margin-left: auto;
  background: #f1f5f9; padding: 4px 10px; border: 1px solid var(--border); border-radius: 4px;
  font-size: 13px;
}
.account-bar input, .account-bar select {
  padding: 4px 8px; border: 1px solid #94a3b8; border-radius: 3px; font: inherit; font-size: 13px; background: #fff;
}
.account-bar button {
  padding: 4px 10px; background: #0078d4; color: #fff; border: 1px solid #005a9e; border-radius: 3px; font-weight: 600; font-size: 13px;
}

/* Thanh địa chỉ đường dẫn (Explorer Address / Breadcrumb Bar) */
.explorer-address-bar {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  padding: 6px 16px; background: #f8fafc; border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.address-box {
  display: flex; align-items: center; gap: 6px; flex: 1 1 360px;
  background: #ffffff; border: 1px solid #94a3b8; border-radius: 3px;
  padding: 5px 10px; font-weight: 600; color: #1e293b;
}
.address-sep { color: #64748b; padding: 0 2px; }

/* Bố cục 2 khung chuẩn Explorer */
.split {
  display: grid;
  grid-template-columns: minmax(460px, 1fr) minmax(520px, 1.15fr);
  gap: 8px;
  padding: 8px 12px;
  align-items: start;
}
@media (max-width: 1080px) {
  .split { grid-template-columns: 1fr; }
}
.pane {
  background: #ffffff;
  border: 1px solid #94a3b8;
  border-radius: 4px;
  overflow: hidden;
}
.pane.left { position: sticky; top: 8px; }
.pane-title {
  margin: 0; padding: 8px 12px; font-size: 14px; font-weight: 700;
  background: var(--bg-ribbon); border-bottom: 1px solid var(--border);
  display: flex; justify-content: space-between; align-items: center;
}

/* KHUNG TRÁI: PDF Viewer */
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 6px 10px; border-bottom: 1px solid var(--border); background: #f8fafc; }
.toolbar button, .chip { min-height: 32px; min-width: 32px; padding: 0 10px; border: 1px solid #94a3b8; background: #fff; border-radius: 3px; font-weight: 600; font-size: 13px; }
.toolbar button:hover, .chip:hover { background: #e5f3ff; border-color: #0078d4; }
.toolbar .spacer { flex: 1 1 0; }
.toolbar .goto { display: flex; align-items: center; gap: 4px; font-size: 13px; font-weight: 600; }
.toolbar .goto input[type=number] { width: 56px; min-height: 32px; padding: 0 6px; border: 1px solid #94a3b8; border-radius: 3px; font: inherit; font-weight: 700; text-align: center; }
.zoom-level { min-width: 44px; text-align: center; font-size: 13px; font-weight: 700; }
.chips { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 6px 10px; font-size: 13px; background: #f0f9ff; border-bottom: 1px solid #bae6fd; }
.chip.current { background: #0078d4; border-color: #005a9e; color: #fff; font-weight: 700; }
.page-scroll { height: 80vh; overflow: auto; padding: 10px; background: #475569; text-align: center; }
.page-scroll img { display: block; margin: 0 auto; max-width: none; background: #fff; box-shadow: 0 3px 10px rgba(0,0,0,0.35); }

/* CÂY THƯ MỤC FILE EXPLORER */
.tree-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 6px 10px; border-bottom: 1px solid var(--border); background: #f8fafc; }
.tree-actions button { min-height: 28px; padding: 0 10px; border: 1px solid #94a3b8; background: #fff; border-radius: 3px; font-size: 12px; font-weight: 600; }
.tree-actions button:hover { background: #e5f3ff; border-color: #0078d4; }
.tree { max-height: 28vh; overflow: auto; padding: 4px 0; background: #ffffff; border-bottom: 2px solid var(--border); }
.node summary { display: flex; align-items: center; list-style: none; }
.node summary::-webkit-details-marker { display: none; }
.caret, .caret-space { flex: none; width: 22px; text-align: center; color: #475569; font-size: 13px; }
.caret { align-self: stretch; display: flex; align-items: center; justify-content: center; cursor: pointer; }
.caret::before { content: "\\25B8"; }
details[open] > summary > .caret::before { content: "\\25BE"; }
.node.leaf { display: flex; align-items: center; }
.children { padding-left: 18px; border-left: 1px dotted #cbd5e1; margin-left: 10px; }
.row {
  display: flex; flex: 1 1 0; min-width: 0; align-items: center; gap: 8px;
  min-height: 32px; padding: 3px 10px 3px 6px; text-align: left;
  border: 1px solid transparent; border-radius: 3px; background: transparent; font-size: 14px;
}
.row:hover { background: #e5f3ff; border-color: #cce8ff; }
.row.selected { background: var(--accent-selected); border-color: #0078d4; font-weight: 600; }
.row .label { font-weight: 700; white-space: nowrap; color: #0f172a; }
.row .snippet { flex: 1 1 0; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #475569; font-size: 13px; }
.dot { display: inline-block; flex: none; width: 10px; height: 10px; border-radius: 5px; }
.st-unreviewed { background: #94a3b8; }
.st-approved { background: #16a34a; }
.st-flagged { background: #d97706; }
.st-structure_error { background: #dc2626; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 14px; padding: 6px 12px; border-bottom: 1px solid var(--border); font-size: 12px; color: #334155; background: #f8fafc; }
.tag.warn { font-size: 12px; font-weight: 600; color: #92400e; background: #fef3c7; border: 1px solid #f59e0b; border-radius: 4px; padding: 1px 6px; white-space: nowrap; }
.tag.edited { font-size: 12px; font-weight: 600; color: #1e40af; background: #dbeafe; border: 1px solid #60a5fa; border-radius: 4px; padding: 1px 6px; }

/* CHẾ ĐỘ SỬA 1 MỤC (TASK 3): Tự động ẩn toàn bộ cây cấu trúc khi đang sửa chữ để tiết kiệm giao diện */
body.editing-single-unit #explorer-tree-panel {
  display: none !important;
}
.single-unit-focus-banner {
  display: none;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #fffbeb;
  border-bottom: 2px solid #f59e0b;
  font-size: 13px;
  font-weight: 700;
  color: #92400e;
}
body.editing-single-unit .single-unit-focus-banner {
  display: flex;
}

/* KHUNG CHI TIẾT & RIBBON THANH CÔNG CỤ KIỂU FILE EXPLORER (KHÔNG POPUP) */
.detail { padding: 12px 14px; }
.detail-head {
  display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center;
  gap: 8px; padding: 8px 10px; background: #f8fafc; border: 1px solid var(--border);
  border-radius: 4px; margin-bottom: 8px; font-size: 16px;
}
.status-line { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 6px 0; font-size: 14px; }

/* Thanh công cụ Ribbon phẳng kiểu File Explorer */
.explorer-ribbon {
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  padding: 8px 10px; background: var(--bg-ribbon); border: 1px solid var(--border);
  border-radius: 4px; margin-bottom: 10px;
}
.ribbon-btn {
  display: inline-flex; align-items: center; gap: 5px;
  min-height: 36px; padding: 0 12px;
  border: 1px solid #94a3b8; background: #ffffff; border-radius: 3px;
  font-weight: 600; font-size: 14px; color: #0f172a;
}
.ribbon-btn:hover { background: #e5f3ff; border-color: #0078d4; }
.ribbon-btn.approve { background: #dcfce7; border-color: #16a34a; color: #14532d; }
.ribbon-btn.approve:hover { background: #bbf7d0; }
.ribbon-btn.edit-toggle { background: #dbeafe; border-color: #2563eb; color: #1e3a8a; }
.ribbon-btn.flag { background: #fef3c7; border-color: #d97706; color: #78350f; }
.ribbon-btn.structure { background: #fee2e2; border-color: #dc2626; color: #7f1d1d; }
.ribbon-btn.save { background: #0078d4; border-color: #005a9e; color: #ffffff; }
.ribbon-btn.save:hover { background: #005a9e; }

.ribbon-inputs {
  display: grid; grid-template-columns: 180px 1fr 1fr; gap: 8px;
  padding: 8px 10px; background: #f8fafc; border: 1px solid var(--border);
  border-radius: 4px; margin-bottom: 10px;
}
@media (max-width: 768px) {
  .ribbon-inputs { grid-template-columns: 1fr; }
}
.field { display: block; margin: 0; font-size: 13px; font-weight: 600; color: #334155; }
.field input, .field select, .field textarea {
  display: block; width: 100%; margin-top: 3px; padding: 6px 8px;
  font: inherit; font-weight: 400; font-size: 14px;
  border: 1px solid #94a3b8; border-radius: 3px; background: #fff;
}

/* Khung đọc & sửa trực tiếp tại chỗ (Inline Editor - Không Popup) */
.content {
  margin: 6px 0 10px;
  padding: 14px 16px;
  background: #fffef7;
  border: 2px solid #94a3b8;
  border-radius: 4px;
  white-space: pre-wrap;
  font-family: "Segoe UI", Tahoma, sans-serif;
  font-size: var(--compare-font-size, 18px);
  line-height: 1.75;
  color: #0f172a;
}
.inline-editor-panel {
  margin: 6px 0 10px;
  padding: 10px;
  background: #eff6ff;
  border: 2px solid #0078d4;
  border-radius: 4px;
}
.inline-editor-panel textarea {
  display: block; width: 100%; padding: 12px 14px;
  font-family: "Segoe UI", Tahoma, sans-serif;
  font-size: var(--compare-font-size, 18px);
  line-height: 1.75;
  border: 1px solid #0078d4; border-radius: 3px;
  background: #ffffff; color: #0f172a;
}

/* Visual Diff */
.diff-box {
  margin: 10px 0; padding: 10px 12px;
  background: #f8fafc; border: 1px solid var(--border); border-left: 4px solid #0078d4; border-radius: 4px;
}
.diff-render {
  white-space: pre-wrap; font-size: var(--compare-font-size, 18px); line-height: 1.7;
  background: #fff; padding: 10px; border: 1px solid #cbd5e1; border-radius: 3px;
}
del.diff-del { background: #fee2e2; color: #991b1b; text-decoration: line-through; padding: 1px 4px; border-radius: 3px; font-weight: 600; }
ins.diff-ins { background: #dcfce7; color: #166534; text-decoration: none; padding: 1px 5px; border-radius: 3px; font-weight: 700; border-bottom: 2px solid #16a34a; }

.note { margin: 8px 0; padding: 8px 12px; background: #fffbeb; border-left: 4px solid #f59e0b; font-size: 14px; }
.review-form ul.err { margin: 0 0 10px; padding: 8px 12px 8px 28px; background: #fef2f2; border: 1px solid #f87171; border-radius: 4px; font-weight: 600; }
.history { margin-top: 10px; border-top: 1px solid var(--border); padding-top: 6px; }
.history summary { cursor: pointer; font-weight: 700; font-size: 13px; color: #334155; }
.history-list { margin: 6px 0 0; padding-left: 20px; font-size: 13px; }
.content.small { font-size: 14px; max-height: 160px; overflow: auto; padding: 8px; }

/* Bảng & Thanh tiến độ */
.doclist { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid var(--border); }
.doclist th { background: #f1f5f9; font-weight: 700; text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 13px; }
.doclist td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #e2e8f0; font-size: 13px; vertical-align: top; }
.progress { display: flex; align-items: center; gap: 10px; min-width: 220px; }
.progress-bar { display: flex; flex: 1 1 110px; height: 10px; background: #e2e8f0; border-radius: 5px; overflow: hidden; }
.progress-bar .seg { height: 100%; }
.progress-text { font-size: 13px; color: #1e293b; white-space: nowrap; }

/* Thẻ chỉ số độ chính xác khoa học (Metric Cards) */
.metrics-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 8px; margin-bottom: 10px;
}
.metric-card {
  background: #f8fafc; border: 1px solid var(--border); border-left: 4px solid #0078d4;
  border-radius: 4px; padding: 8px 10px;
}
.metric-card .m-title { font-size: 12px; color: #475569; font-weight: 600; }
.metric-card .m-val { font-size: 18px; font-weight: 800; color: #0f172a; margin: 2px 0; }
.metric-card .m-sub { font-size: 11px; color: #64748b; }
'''
(ROOT / "app" / "static" / "style.css").write_text(STYLE_CSS.strip() + "\n", encoding="utf-8")


# ==============================================================================
# 6. CẬP NHẬT TEMPLATES: tree.html, detail.html, ocr_report.html, review.html, list.html
# ==============================================================================
TREE_HTML = '''{#- Cây thư mục kiểu Windows File Explorer -#}
{% macro row(n) -%}
<button type="button" class="row{% if n.selected %} selected{% endif %}"{% if n.selected %} aria-current="true"{% endif %}
        hx-get="{{ n.url }}" hx-target="#detail" hx-swap="innerHTML"
        hx-on:click="document.body.classList.remove('editing-single-unit'); document.querySelectorAll('#tree .row.selected').forEach(function (e) { e.classList.remove('selected'); e.removeAttribute('aria-current'); }); this.classList.add('selected'); this.setAttribute('aria-current', 'true');">
  <span id="dot-{{ n.unit_id }}" class="dot st-{{ n.review_status }}" title="{{ n.review_status_label }}" role="img" aria-label="{{ n.review_status_label }}"></span>
  <span aria-hidden="true">{% if n.children %}📁{% else %}📄{% endif %}</span>
  <span class="label">{{ n.label }}</span>
  <span class="snippet">{{ n.snippet }}</span>
  {% if not n.is_confident %}<span class="tag warn">Không chắc</span>{% endif %}
</button>
{%- endmacro %}

{% macro node(n) -%}
{% if n.children %}
<details class="node"{% if n.open %} open{% endif %}>
  <summary><span class="caret" aria-hidden="true"></span>{{ row(n) }}</summary>
  <div class="children">{% for child in n.children %}{{ node(child) }}{% endfor %}</div>
</details>
{% else %}
<div class="node leaf"><span class="caret-space" aria-hidden="true"></span>{{ row(n) }}</div>
{% endif %}
{%- endmacro %}

<div id="explorer-tree-panel">
  <div class="tree-actions">
    <strong class="small" style="margin-right:auto;">📂 Cây thư mục pháp lý:</strong>
    <button type="button" onclick="document.querySelectorAll('#tree details').forEach(function (d) { d.open = true; })">⊞ Mở hết</button>
    <button type="button" onclick="document.querySelectorAll('#tree details').forEach(function (d) { d.open = false; })">⊟ Thu hết</button>
  </div>
  <div id="tree" class="tree">
    {% for root in tree %}{{ node(root) }}{% endfor %}
  </div>
  <div class="legend">
    <span><span class="dot st-unreviewed"></span>Chưa soát</span>
    <span><span class="dot st-approved"></span>Đã duyệt</span>
    <span><span class="dot st-flagged"></span>Gắn cờ</span>
    <span><span class="dot st-structure_error"></span>Lỗi cấu trúc</span>
  </div>
</div>
'''
(ROOT / "app" / "templates" / "partials" / "tree.html").write_text(TREE_HTML, encoding="utf-8")


DETAIL_HTML = '''{% if unit %}
{% set status = review.status if review else 'unreviewed' %}
{% set status_label = review.status_label if review else 'Chưa soát' %}

<!-- Banner hiển thị khi đang ở chế độ Sửa 1 mục (ẩn toàn bộ cây cấu trúc để tiết kiệm diện tích) -->
<div class="single-unit-focus-banner">
  <span>🎯 Chế độ sửa 1 mục: Đã thu gọn cây cấu trúc để tập trung đối chiếu <strong>{{ unit.unit_id }}</strong> với Trang {{ unit.source_pages|join(", ") }}</span>
  <button type="button" class="ribbon-btn" onclick="exitSingleUnitEditMode()">📂 Hiện lại cây cấu trúc</button>
</div>

<!-- Thanh đường dẫn File Explorer Breadcrumb -->
<div class="detail-head">
  <div>
    <span class="muted small">📁 {{ doc.document_id }} {% if unit.chapter %}&rsaquo; Chương {{ unit.chapter }}{% endif %} &rsaquo;</span>
    <strong>{% if unit.point is not none %}Điểm {{ unit.point }}, {% endif %}{% if unit.clause is not none %}Khoản {{ unit.clause }}, {% endif %}{% if unit.article is not none %}Điều {{ unit.article }}{% else %}{{ unit.unit_id }}{% endif %}</strong>
  </div>
  <div style="display:flex; align-items:center; gap:8px;">
    <span class="muted small">Trang {{ unit.source_pages|join(", ") }}</span>
    <button type="button" class="ribbon-btn" style="min-height:28px; padding:0 8px; font-size:12px;" onclick="changeCompareFont(-2)">A-</button>
    <button type="button" class="ribbon-btn" style="min-height:28px; padding:0 8px; font-size:12px;" onclick="changeCompareFont(2)">A+</button>
  </div>
</div>

{% if unit.title %}<div class="muted" style="font-weight:600; margin-bottom:6px;">{{ unit.title }}</div>{% endif %}

<div class="status-line">
  <span class="dot st-{{ status }}" aria-hidden="true"></span><strong>{{ status_label }}</strong>
  {% if review and review.edit_count %}<span class="tag edited">Đã sửa chữ ({{ review.edit_count }} lần)</span>{% endif %}
  {% if not unit.is_confident %}<span class="tag warn">Parser đánh dấu không chắc chắn</span>{% endif %}
  {% if review and review.last %}<span class="muted small">· {{ review.last.action_label }} bởi <strong>{{ review.last.reviewer }}</strong>, {{ review.last.created_at|vn_time }}</span>{% endif %}
</div>
{% if review and review.note %}<p class="note">Ghi chú / Lý do sai: {{ review.note }}</p>{% endif %}

{% if can_review %}
<form id="review-form" class="review-form" style="margin-top:8px; padding-top:0; border:0;" onsubmit="return false;">
  {% if errors %}<ul class="err" role="alert">{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul>{% endif %}
  {% set url = "/documents/" ~ doc.document_id ~ "/units/" ~ (unit.unit_id|urlencode) ~ "/review" %}

  <!-- Thanh công cụ phẳng kiểu File Explorer Ribbon (Không dùng pop-up) -->
  <div class="explorer-ribbon">
    <button type="button" class="ribbon-btn approve" onclick="exitSingleUnitEditMode()" hx-post="{{ url }}" hx-vals='{"action": "approve"}' hx-include="#review-form" hx-target="#detail" hx-swap="innerHTML">✓ Duyệt</button>
    <button type="button" class="ribbon-btn edit-toggle" onclick="enterSingleUnitEditMode()">✎ Sửa chữ (Chỉ hiện mục này)</button>
    <button type="button" class="ribbon-btn flag" onclick="exitSingleUnitEditMode()" hx-post="{{ url }}" hx-vals='{"action": "flag"}' hx-include="#review-form" hx-target="#detail" hx-swap="innerHTML">⚑ Gắn cờ</button>
    <button type="button" class="ribbon-btn structure" onclick="exitSingleUnitEditMode()" hx-post="{{ url }}" hx-vals='{"action": "structure_error"}' hx-include="#review-form" hx-target="#detail" hx-swap="innerHTML">⚠ Báo lỗi cấu trúc</button>
    {% if status != 'unreviewed' %}
    <button type="button" class="ribbon-btn" onclick="exitSingleUnitEditMode()" hx-post="{{ url }}" hx-vals='{"action": "reset"}' hx-include="#review-form" hx-target="#detail" hx-swap="innerHTML">↺ Bỏ đánh dấu</button>
    {% endif %}
  </div>

  <!-- Hàng thuộc tính phẳng (Tài khoản người soát + Lý do lỗi) -->
  <div class="ribbon-inputs">
    <label class="field">👤 Tài khoản người soát
      <input type="text" name="reviewer" value="{{ reviewer }}" maxlength="100" autocomplete="name" placeholder="Tên tài khoản" required>
    </label>
    <label class="field">🏷 Phân loại nguyên nhân OCR sai
      <select name="ocr_reason">
        <option value="">-- Chọn loại lỗi OCR (nếu sửa chữ) --</option>
        {% for r in OCR_REASONS or [
          "Mờ/nhòe bản scan",
          "Sai dấu tiếng Việt",
          "Nhầm ký tự giống nhau (d/đ, 0/O, 1/l...)",
          "Mất chữ / dính chữ",
          "Lỗi công thức / ký hiệu / bảng biểu"
        ] %}
        <option value="{{ r }}">{{ r }}</option>
        {% endfor %}
      </select>
    </label>
    <label class="field">📝 Ghi chú / Lý do chi tiết <span class="muted">(bắt buộc khi Gắn cờ / Lỗi cấu trúc)</span>
      <input type="text" name="note" value="{{ form_note }}" maxlength="2000" placeholder="Ghi lý do sai hoặc vị trí lỗi...">
    </label>
  </div>

  <!-- Chế độ xem chuẩn -->
  <div id="read-only-view"{% if form_content is not none %} style="display:none;"{% endif %}>
    <pre class="content" title="Bấm 'Sửa chữ' trên thanh công cụ để sửa trực tiếp mục này">{{ unit.content }}</pre>
  </div>

  <!-- Chế độ Sửa tại chỗ 1 mục (Không pop-up, ẩn cây cấu trúc để tiết kiệm diện tích) -->
  <details id="inline-edit-details" class="edit-box"{% if form_content is not none %} open{% endif %} style="margin:0; padding:0; border:0; background:transparent;">
    <summary style="display:none;">Sửa chữ</summary>
    <div class="inline-editor-panel">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
        <strong style="color:#005a9e;">✎ Đang sửa trực tiếp mục: {{ unit.unit_id }} (Đối chiếu với Trang {{ unit.source_pages|join(", ") }})</strong>
        <div style="display:flex; gap:6px;">
          <button type="button" class="ribbon-btn save" onclick="exitSingleUnitEditMode()" hx-post="{{ url }}" hx-vals='{"action": "edit"}' hx-include="#review-form" hx-target="#detail" hx-swap="innerHTML">💾 Lưu bản sửa</button>
          <button type="button" class="ribbon-btn" onclick="exitSingleUnitEditMode()">✕ Đóng sửa &amp; Hiện lại cây</button>
        </div>
      </div>
      <textarea id="inline-edit-textarea" name="content" rows="12" spellcheck="false">{{ form_content if form_content is not none else unit.content }}</textarea>
      <p class="muted small" style="margin:4px 0 0;">Bản gốc vẫn được giữ trong lịch sử; sửa một đơn vị đã duyệt thì phải duyệt lại.</p>
    </div>
  </details>
</form>

{% if inline_diff %}
<div class="diff-box">
  <div style="font-weight:700; font-size:13px; color:#005a9e; margin-bottom:4px;">🔍 Chỗ hệ thống OCR đọc sai (Đỏ = Bản gốc sai, Xanh = Bản đã sửa):</div>
  <div class="diff-render">{{ inline_diff|safe }}</div>
</div>
{% endif %}

<div class="muted small" style="margin-top:4px;">ID: {{ unit.unit_id }}</div>

<details class="history">
  <summary>🕒 Lịch sử thao tác ({{ history|length }})</summary>
  {% if not history %}<p class="muted small">Chưa có thao tác nào.</p>{% endif %}
  <ol class="history-list">
    {% for e in history %}
    <li>
      <span class="small"><strong>{{ e.action_label }}</strong> · {{ e.reviewer }} · {{ e.created_at|vn_time }}</span>
      {% if e.note %}<div class="small">Ghi chú: {{ e.note }}</div>{% endif %}
      {% if e.action == 'edit' %}
      <details class="small"><summary>Xem chữ trước và sau khi sửa</summary>
        <div class="muted">Trước:</div><pre class="content small">{{ e.content_before }}</pre>
        <div class="muted">Sau:</div><pre class="content small">{{ e.content_after }}</pre>
      </details>
      {% endif %}
    </li>
    {% endfor %}
  </ol>
</details>
{% else %}
<pre class="content">{{ unit.content }}</pre>
<div class="muted small">{{ unit.unit_id }}</div>
<p class="muted small">Chế độ xem thư mục file: chưa lưu được thao tác thẩm định (cần PostgreSQL, xem README).</p>
{% endif %}
{% else %}
<p class="muted">Văn bản chưa có đơn vị nào.</p>
{% endif %}
'''
(ROOT / "app" / "templates" / "partials" / "detail.html").write_text(DETAIL_HTML, encoding="utf-8")


OCR_REPORT_HTML = '''<div id="ocr-report-box" style="padding: 6px 12px 0;"{% if oob %} hx-swap-oob="true"{% endif %}>
{% if can_review and progress and progress.ocr_report and (progress.approved > 0 or progress.ocr_report.edited_units > 0) %}
  {% set rep = progress.ocr_report %}
  <details style="background:#fff; border:1px solid #94a3b8; border-radius:4px; padding:8px 12px;">
    <summary style="cursor:pointer; font-weight:700; font-size:14px; color:#0f172a;">
      📐 Thang Đo Độ Chính Xác Hệ Thống (Chuẩn ICDAR: CER/WER &amp; Đề Xuất Pháp Lý: UEM/SFI)
      {%- if rep.composite_score is not none %} — Điểm tổng hợp: <strong style="color:#16a34a;">{{ rep.composite_score }}%</strong>{% endif %}
      (Đã ghi nhận {{ rep.edited_units }} mục sửa lỗi OCR)
    </summary>
    <div style="margin-top:10px;">
      <div class="metrics-grid">
        <div class="metric-card">
          <div class="m-title">1. Độ chính xác Ký tự (CAR)</div>
          <div class="m-val">{{ rep.char_accuracy_percent if rep.char_accuracy_percent is not none else "—" }}%</div>
          <div class="m-sub">CER = {{ rep.cer_percent if rep.cer_percent is not none else "—" }}% (Chuẩn UNLV/ICDAR)</div>
        </div>
        <div class="metric-card">
          <div class="m-title">2. Độ chính xác Từ (WAR)</div>
          <div class="m-val">{{ rep.word_accuracy_percent if rep.word_accuracy_percent is not none else "—" }}%</div>
          <div class="m-sub">WER = {{ rep.wer_percent if rep.wer_percent is not none else "—" }}% (Đo sai dấu từ TV)</div>
        </div>
        <div class="metric-card">
          <div class="m-title">3. Đơn vị nguyên vẹn (UEM)</div>
          <div class="m-val">{{ rep.unit_accuracy_percent if rep.unit_accuracy_percent is not none else "—" }}%</div>
          <div class="m-sub">{{ rep.exact_match_units }}/{{ rep.approved_units }} mục duyệt không cần sửa</div>
        </div>
        <div class="metric-card">
          <div class="m-title">4. Toàn vẹn cấu trúc (SFI)</div>
          <div class="m-val">{{ rep.structural_fidelity_percent if rep.structural_fidelity_percent is not none else "—" }}%</div>
          <div class="m-sub">Đo độ chính xác tách Điều/Khoản/Điểm</div>
        </div>
        <div class="metric-card" style="border-left-color:#16a34a; background:#f0fdf4;">
          <div class="m-title">5. Điểm Hệ Thống Đề Xuất</div>
          <div class="m-val" style="color:#15803d;">{{ rep.composite_score if rep.composite_score is not none else "—" }}%</div>
          <div class="m-sub">0.4·CAR + 0.3·WAR + 0.2·SFI + 0.1·UEM</div>
        </div>
      </div>

      {% if rep.common_substitutions %}
      <table class="doclist" style="margin-bottom:8px;">
        <thead>
          <tr><th>Ký tự OCR đọc sai</th><th>Ký tự đúng</th><th>Số lần</th><th>Giải pháp cải tiến độ chính xác</th></tr>
        </thead>
        <tbody>
        {% for item in rep.common_substitutions %}
          <tr>
            <td><del class="diff-del">{{ item.ocr_read or "(bỏ sót)" }}</del></td>
            <td><ins class="diff-ins">{{ item.corrected_to or "(ký tự thừa)" }}</ins></td>
            <td><strong>{{ item.count }}</strong></td>
            <td>{{ item.solution }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      {% endif %}
    </div>
  </details>
{% endif %}
</div>
'''
(ROOT / "app" / "templates" / "partials" / "ocr_report.html").write_text(OCR_REPORT_HTML, encoding="utf-8")


REVIEW_HTML = '''{% extends "base.html" %}
{% from "partials/progress.html" import bar %}
{% block title %}{{ doc.document_no }}{% endblock %}
{% block body %}
<header class="topbar">
  <a href="/" class="button-link">&larr; Danh sách văn bản</a>
  <h1>🗂 {{ doc.title }} <span class="muted">· {{ doc.document_no }}</span></h1>
  <div id="progress">{{ bar(progress) }}</div>

  <!-- Quản lý tài khoản & lưu tiến trình cá nhân -->
  <form class="account-bar" method="post" action="/account/login">
    <input type="hidden" name="redirect_to" value="/documents/{{ doc.document_id }}">
    <span>👤 Tài khoản:</span>
    <input type="text" name="username" value="{{ reviewer }}" list="account-list" placeholder="Nhập tên tài khoản..." required>
    <datalist id="account-list">
      {% for acc in accounts %}<option value="{{ acc.username }}">{{ acc.display_name }}</option>{% endfor %}
    </datalist>
    <button type="submit">Lưu / Chuyển TK</button>
  </form>

  {% if can_review %}<a class="button-link" href="/api/documents/{{ doc.document_id }}/export" download>⬇ Xuất JSON đã duyệt</a>{% endif %}
</header>

{% include "partials/ocr_report.html" %}

<div class="split">
  <section class="pane left" aria-label="Trang PDF gốc">
    <h2 class="pane-title">
      <span>📄 Bản gốc PDF Scan</span>
      {% if reviewer %}<span class="muted small">Đang lưu tiến trình của: <strong>{{ reviewer }}</strong></span>{% endif %}
    </h2>
    {% include "partials/viewer.html" %}
  </section>
  <section class="pane right">
    <h2 class="pane-title">
      <span>🗂 File Explorer: Cấu trúc Điều &rarr; Khoản &rarr; Điểm</span>
    </h2>
    {% include "partials/tree.html" %}
    <div id="detail" class="detail" aria-live="polite">
      {% include "partials/detail.html" %}
    </div>
  </section>
</div>

<script>
  function changeCompareFont(delta) {
    var root = document.documentElement;
    var current = parseInt(getComputedStyle(root).getPropertyValue("--compare-font-size")) || 18;
    var next = Math.min(Math.max(current + delta, 14), 26);
    root.style.setProperty("--compare-font-size", next + "px");
    localStorage.setItem("compareFontSize", next);
  }
  function enterSingleUnitEditMode() {
    document.body.classList.add("editing-single-unit");
    var readView = document.getElementById("read-only-view");
    var editDetails = document.getElementById("inline-edit-details");
    if (readView) readView.style.display = "none";
    if (editDetails) editDetails.open = true;
    var ta = document.getElementById("inline-edit-textarea");
    if (ta) ta.focus();
  }
  function exitSingleUnitEditMode() {
    document.body.classList.remove("editing-single-unit");
    var readView = document.getElementById("read-only-view");
    var editDetails = document.getElementById("inline-edit-details");
    if (readView) readView.style.display = "block";
    if (editDetails) editDetails.open = false;
  }
  window.addEventListener("DOMContentLoaded", function () {
    var savedSize = localStorage.getItem("compareFontSize");
    if (savedSize) {
      document.documentElement.style.setProperty("--compare-font-size", savedSize + "px");
    }
    var selectedRow = document.querySelector("#tree .row.selected");
    if (selectedRow && selectedRow.scrollIntoView) {
      selectedRow.scrollIntoView({ block: "center" });
    }
  });
</script>
{% endblock %}
'''
(ROOT / "app" / "templates" / "review.html").write_text(REVIEW_HTML, encoding="utf-8")


LIST_HTML = '''{% extends "base.html" %}
{% from "partials/progress.html" import bar %}
{% block title %}Danh sách văn bản{% endblock %}
{% block body %}
<header class="topbar">
  <h1>🗂 Hệ thống Thẩm định Dữ liệu Pháp lý (File Explorer)</h1>
  <form class="account-bar" method="post" action="/account/login">
    <input type="hidden" name="redirect_to" value="/">
    <span>👤 Tài khoản người soát:</span>
    <input type="text" name="username" value="{{ reviewer }}" list="account-list" placeholder="Nhập tên để lưu tiến trình..." required>
    <datalist id="account-list">
      {% for acc in accounts %}<option value="{{ acc.username }}">{{ acc.display_name }}</option>{% endfor %}
    </datalist>
    <button type="submit">{% if reviewer %}Đổi tài khoản{% else %}Đăng nhập / Tạo TK{% endif %}</button>
  </form>
</header>
<main class="narrow">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
    <h2 style="margin:0;">📂 Thư mục Văn bản cần soát</h2>
    {% if reviewer %}
      <span class="tag edited" style="font-size:14px; padding:4px 10px;">
        Đang làm việc dưới tài khoản: <strong>{{ reviewer }}</strong> (Tự động nhảy tới mục đang duyệt dở khi mở văn bản)
      </span>
    {% endif %}
  </div>
  {% if not items %}
  <p class="muted">Chưa có văn bản nào. Nhập bằng lệnh <code>python -m app.cli import-dir &lt;thư mục&gt;</code> hoặc qua API <code>POST /api/documents</code> (xem README).</p>
  {% else %}
  <table class="doclist">
    <thead>
      <tr><th>Văn bản</th><th>Số đơn vị</th><th>Số trang</th><th>Tiến độ thẩm định &amp; Độ chính xác</th><th>Tiến trình của {{ reviewer or "bạn" }}</th></tr>
    </thead>
    <tbody>
    {% for item in items %}
      {% if not item.problem %}
      {% set up = user_progress_map.get(item.document_id) if user_progress_map else None %}
      <tr>
        <td>
          📁 <a href="/documents/{{ item.document_id }}">{{ item.title }}</a><br>
          <span class="muted">{{ item.document_no }}</span>
        </td>
        <td>{{ item.unit_count }}</td>
        <td>{{ item.page_count }}</td>
        <td>{{ bar(item.progress) }}</td>
        <td>
          {% if up %}
            <a class="button-link" href="/documents/{{ item.document_id }}?unit={{ up.last_unit_id }}&page={{ up.last_page }}">
              ▶ Tiếp tục tại {{ up.last_unit_id }} (Trang {{ up.last_page }})
            </a>
          {% else %}
            <a href="/documents/{{ item.document_id }}">Mở văn bản &rarr;</a>
          {% endif %}
        </td>
      </tr>
      {% else %}
      <tr>
        <td colspan="5">
          <strong>{{ item.document_id }}</strong>: <span class="err">không nhập được</span>
          (<a href="/documents/{{ item.document_id }}">xem lỗi</a>)
        </td>
      </tr>
      {% endif %}
    {% endfor %}
    </tbody>
  </table>
  {% endif %}
</main>
{% endblock %}
'''
(ROOT / "app" / "templates" / "list.html").write_text(LIST_HTML, encoding="utf-8")

print("Đã cập nhật xong cả 4 task (Thang đo khoa học + Giao diện File Explorer + Sửa 1 mục ẩn cây + Tài khoản lưu tiến trình)!")