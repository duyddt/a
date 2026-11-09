"""Thẩm định: quy tắc thuần Python (không phụ thuộc FastAPI hay DB).

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
    """Trình duyệt gửi xuống dòng kiểu \r\n; chuẩn hóa về \n để so sánh và lưu."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


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
