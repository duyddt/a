"""Quy tắc thẩm định (thuần Python) và giao diện khung chi tiết (render template bằng Jinja2, không cần FastAPI)."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.contracts import LegalDocument
from app.services.models import LoadedDocument
from app.services.review import (
    ReviewEvent, ReviewProblem, UnitReview, apply_edits, build_export, decide, fold_events, progress,
)
from app.services.tree import build_tree
from app.services.views import vn_time

U = "ND254_2026_D9"
T0 = datetime(2026, 10, 31, 0, 45, tzinfo=timezone.utc)


def ev(i, action, unit=U, reviewer="Duy", note=None, before=None, after=None):
    return ReviewEvent(id=i, unit_id=unit, action=action, reviewer=reviewer, note=note,
                       content_before=before, content_after=after, created_at=T0)


def problems_of(*args, **kwargs):
    with pytest.raises(ReviewProblem) as exc:
        decide(*args, **kwargs)
    return " | ".join(exc.value.problems)


# ---- trạng thái tính từ lịch sử ----

def test_no_events_means_unreviewed():
    assert fold_events([]) == {}


def test_status_follows_latest_event():
    reviews = fold_events([ev(1, "approve"), ev(2, "flag", note="sai số"), ev(3, "structure_error", note="thiếu Khoản"), ev(4, "approve")])
    assert reviews[U].status == "approved" and reviews[U].note is None
    reviews = fold_events([ev(1, "flag", note="sai số")])
    assert reviews[U].status == "flagged" and reviews[U].note == "sai số"
    assert fold_events([ev(1, "approve"), ev(2, "reset")])[U].status == "unreviewed"


def test_units_are_independent():
    reviews = fold_events([ev(1, "approve", unit="A"), ev(2, "flag", unit="B", note="x")])
    assert (reviews["A"].status, reviews["B"].status) == ("approved", "flagged")


def test_edit_changes_text_and_cancels_approval_but_keeps_flags():
    reviews = fold_events([ev(1, "approve"), ev(2, "edit", before="cũ", after="mới")])
    assert reviews[U].status == "unreviewed" and reviews[U].edited_content == "mới" and reviews[U].edit_count == 1
    reviews = fold_events([ev(1, "flag", note="chữ sai"), ev(2, "edit", before="cũ", after="mới")])
    assert reviews[U].status == "flagged" and reviews[U].note == "chữ sai"
    reviews = fold_events([ev(1, "edit", before="a", after="b"), ev(2, "edit", before="b", after="c")])
    assert reviews[U].edited_content == "c" and reviews[U].edit_count == 2
    assert fold_events([ev(1, "edit", before="a", after="b"), ev(2, "approve")])[U].status == "approved"  # duyệt chữ mới


# ---- kiểm tra thao tác mới ----

def test_decide_accepts_valid_actions():
    assert decide("gốc", [], "approve", "  Duy ")["reviewer"] == "Duy"
    flag = decide("gốc", [], "flag", "Duy", note=" thiếu chữ ")
    assert flag["note"] == "thiếu chữ" and flag["content_after"] is None
    edit = decide("dòng 1\ndòng 2", [], "edit", "Duy", content="dòng 1\r\ndòng 2 đã sửa")
    assert edit["content_before"] == "dòng 1\ndòng 2" and edit["content_after"] == "dòng 1\ndòng 2 đã sửa"


def test_decide_rejects_bad_input():
    assert "Cần nhập tên người soát" in problems_of("x", [], "approve", "   ")
    assert "cần ghi chú" in problems_of("x", [], "flag", "Duy")
    assert "cần ghi chú" in problems_of("x", [], "structure_error", "Duy", note="  ")
    assert "không hợp lệ" in problems_of("x", [], "delete", "Duy")
    assert "quá dài" in problems_of("x", [], "approve", "D" * 101)
    assert "không được để trống" in problems_of("x", [], "edit", "Duy", content="  \n ")
    assert "không thay đổi" in problems_of("gốc", [], "edit", "Duy", content="gốc")
    assert "không thay đổi" in problems_of("a\nb", [], "edit", "Duy", content="a\r\nb")  # \r\n không tính là thay đổi


def test_edit_compares_against_current_text_not_original():
    prior = [ev(1, "edit", before="gốc", after="đã sửa")]
    assert "không thay đổi" in problems_of("gốc", prior, "edit", "Duy", content="đã sửa")
    again = decide("gốc", prior, "edit", "Duy", content="gốc")  # sửa ngược về chữ gốc là một thay đổi hợp lệ
    assert again["content_before"] == "đã sửa" and again["content_after"] == "gốc"


# ---- áp dụng bản sửa, tiến độ, xuất ----

@pytest.fixture
def doc(sample_json):
    return LegalDocument.model_validate(json.loads(sample_json))


def test_apply_edits_does_not_touch_the_original(doc):
    reviews = fold_events([ev(1, "edit", unit="ND254_2026_D10_K1", before="x", after="CHỮ ĐÃ SỬA")])
    edited = apply_edits(doc.legal_units, reviews)
    assert {u.unit_id: u.content for u in edited}["ND254_2026_D10_K1"] == "CHỮ ĐÃ SỬA"
    assert {u.unit_id: u.content for u in doc.legal_units}["ND254_2026_D10_K1"] != "CHỮ ĐÃ SỬA"
    assert [u.unit_id for u in edited] == [u.unit_id for u in doc.legal_units]  # giữ thứ tự


def test_progress_counts_by_status(doc):
    ids = [u.unit_id for u in doc.legal_units]
    reviews = fold_events([ev(1, "approve", unit=ids[0]), ev(2, "approve", unit=ids[1]),
                           ev(3, "flag", unit=ids[2], note="x"), ev(4, "structure_error", unit=ids[3], note="y"),
                           ev(5, "approve", unit="khong_con_ton_tai")])
    p = progress(ids, reviews)
    assert p == {"total": 7, "approved": 2, "flagged": 1, "structure_error": 1, "unreviewed": 3, "percent_approved": 29}
    assert progress([], {})["percent_approved"] == 0


def test_export_has_only_approved_units_with_current_text(doc):
    ids = [u.unit_id for u in doc.legal_units]
    reviews = fold_events([
        ev(1, "approve", unit=ids[0]),
        ev(2, "edit", unit=ids[1], before="x", after="đã sửa"), ev(3, "approve", unit=ids[1]),
        ev(4, "approve", unit=ids[2]), ev(5, "flag", unit=ids[2], note="sai"),  # bị gắn cờ: không xuất
        ev(6, "approve", unit=ids[3]), ev(7, "edit", unit=ids[3], before="x", after="y"),  # sửa sau khi duyệt: không xuất
    ])
    data, stats = build_export(apply_edits_doc(doc, reviews), reviews)
    assert [u["unit_id"] for u in data["legal_units"]] == [ids[0], ids[1]]
    assert data["legal_units"][1]["content"] == "đã sửa"
    assert (stats["exported"], stats["total"]) == (2, 7)
    again = LegalDocument.model_validate(json.loads(json.dumps(data, ensure_ascii=False)))  # vẫn đọc được bằng contract
    assert again.document_id == doc.document_id and len(again.legal_units) == 2
    assert not any(k in data for k in ("review", "reviews", "export_info"))


def test_export_reports_children_whose_parent_is_not_exported(doc):
    child = next(u for u in doc.legal_units if u.parent_unit_id)
    data, stats = build_export(doc, fold_events([ev(1, "approve", unit=child.unit_id)]))
    assert [u["unit_id"] for u in data["legal_units"]] == [child.unit_id]
    assert stats["parent_not_exported"] == [child.unit_id]


def apply_edits_doc(doc, reviews):
    copy = doc.model_copy()
    copy.legal_units = apply_edits(doc.legal_units, reviews)
    return copy


# ---- cây và giao diện ----

def test_tree_uses_review_status(doc):
    reviews = fold_events([ev(1, "flag", unit="ND254_2026_D9_K1_a", note="x"), ev(2, "approve", unit="ND254_2026_D10")])
    tree = build_tree(doc.legal_units, reviews)
    d9, d10 = tree
    assert d10["review_status"] == "approved" and d9["review_status"] == "unreviewed"
    assert d9["children"][0]["children"][0]["review_status"] == "flagged"
    assert d9["children"][0]["children"][0]["review_status_label"] == "Gắn cờ"
    assert build_tree(doc.legal_units)[0]["review_status"] == "unreviewed"  # không truyền reviews = chưa soát hết


def test_vn_time_is_utc_plus_7():
    assert vn_time(T0) == "31/10/2026 07:45" and vn_time(None) == ""


@pytest.fixture
def env():
    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    e = Environment(loader=FileSystemLoader(templates), autoescape=select_autoescape(["html"]))
    e.filters["vn_time"] = vn_time
    return e


def render_detail(env, doc, **extra):
    unit = next(u for u in doc.legal_units if u.unit_id == "ND254_2026_D9_K1_a")
    context = dict(doc=doc, unit=unit, can_review=True, review=None, history=[], reviewer="", errors=[],
                   form_note="", form_content=None)
    context.update(extra)
    return env.get_template("partials/detail.html").render(**context)


def test_detail_shows_buttons_and_hides_reset_when_unreviewed(env, doc):
    html = render_detail(env, doc)
    for label in ("Duyệt", "Gắn cờ", "Báo lỗi cấu trúc", "Sửa chữ", "Lưu bản sửa"):
        assert label in html
    assert "Bỏ đánh dấu" not in html and "Chưa soát" in html
    assert 'hx-post="/documents/ND254_2026/units/ND254_2026_D9_K1_a/review"' in html
    assert '"action": "approve"' in html and 'onsubmit="return false;"' in html


def test_detail_shows_status_note_history_and_edit_marker(env, doc):
    unit_id = "ND254_2026_D9_K1_a"
    events = [ev(1, "edit", unit=unit_id, reviewer="Dũng", before="cũ", after="mới"),
              ev(2, "flag", unit=unit_id, reviewer="Duy", note="lệch với trang 1")]
    review = fold_events(events)[unit_id]
    html = render_detail(env, doc, review=review, history=list(reversed(events)), reviewer="Duy")
    assert "Gắn cờ" in html and "lệch với trang 1" in html and "Đã sửa chữ (1 lần)" in html
    assert "Bỏ đánh dấu" in html and "Lịch sử thao tác (2)" in html and "Dũng" in html
    assert "31/10/2026 07:45" in html and 'value="Duy"' in html


def test_detail_escapes_user_text_and_keeps_form_values_after_error(env, doc):
    html = render_detail(env, doc, errors=["Cần nhập tên người soát"], form_note="<script>x</script>", form_content="chữ đang gõ dở")
    assert "Cần nhập tên người soát" in html and "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert "chữ đang gõ dở" in html and "<details class=\"edit-box\" open>" in html


def test_detail_without_database_has_no_buttons(env, doc):
    html = render_detail(env, doc, can_review=False)
    assert "Duyệt" not in html and "PostgreSQL" in html


def test_progress_bar_and_review_response_render(env, doc):
    ids = [u.unit_id for u in doc.legal_units]
    reviews = fold_events([ev(1, "approve", unit=ids[0])])
    p = progress(ids, reviews)
    html = env.get_template("partials/progress.html").module.bar(p)
    assert "Đã duyệt 1/7 (14%)" in str(html)
    assert str(env.get_template("partials/progress.html").module.bar(None)).strip() == ""

    unit = doc.legal_units[0]
    response = env.get_template("review_response.html").render(
        doc=doc, unit=unit, can_review=True, review=reviews[ids[0]], history=[], reviewer="Duy", errors=[],
        form_note="", form_content=None, progress=p)
    assert f'id="dot-{ids[0]}"' in response and "st-approved" in response and 'hx-swap-oob="true"' in response
    assert 'id="progress"' in response


def test_detail_modal_popups_and_pastel_palette_elements(env, doc):
    html = render_detail(env, doc)
    for modal_id in ("modal-edit", "modal-flag", "modal-structure", "modal-history"):
        assert f'id="{modal_id}"' in html
    for struct_type in ("Sai cấp", "Sai số", "Cần tách", "Cần gộp"):
        assert struct_type in html
    for pastel_cls in ("pastel-btn", "btn-approve", "btn-edit", "btn-flag", "btn-structure", "btn-history"):
        assert pastel_cls in html


def test_ocr_report_modal_renders_accuracy_and_substitutions(env):
    p = {
        "total": 10,
        "char_accuracy_percent": 98.7,
        "ocr_report": {
            "edited_units": 2,
            "common_substitutions": [
                {"ocr_read": "dieu", "corrected_to": "điều", "count": 3, "solution": "Nâng cấp mô hình từ điển"}
            ],
            "unit_errors": [
                {
                    "unit_id": "ND254_2026_D1",
                    "pages": [1],
                    "diff_html": "<del>dieu</del><ins>điều</ins>",
                    "reasons": ["Sai dấu tiếng Việt"],
                    "solution": "Nâng cấp mô hình từ điển",
                }
            ],
        },
    }
    html = env.get_template("partials/ocr_report.html").render(can_review=True, progress=p, oob=False)
    assert 'id="modal-ocr-report"' in html
    assert "98.7%" in html
    assert "2 / 10" in html
    assert "Nâng cấp mô hình từ điển" in html
