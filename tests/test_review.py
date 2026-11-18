"""Quy tắc thẩm định (thuần Python) và giao diện khung chi tiết (render template bằng Jinja2, không cần FastAPI)."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from app.services.review import (
    ReviewEvent,
    ReviewProblem,
    apply_edits,
    build_export,
    decide,
    fold_events,
    fold_tracks,
    progress,
)
from app.services.tree import build_tree, full_label, neighbours, next_unreviewed, tree_order
from app.services.views import vn_time
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.contracts import LegalDocument

U = "ND254_2026_D9"
T0 = datetime(2026, 10, 31, 0, 45, tzinfo=timezone.utc)


def ev(i, action, unit=U, reviewer="Duy", note=None, before=None, after=None, track="final"):
    return ReviewEvent(id=i, unit_id=unit, action=action, reviewer=reviewer, note=note,
                       content_before=before, content_after=after, created_at=T0, track=track)


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


# ---- bản riêng của từng người: OCR gốc, bản của mỗi người chỉnh sửa, bản chốt ----

def test_each_reviewer_and_the_final_version_are_folded_separately():
    events = [ev(1, "edit", reviewer="bt1", before="gốc", after="A", track="editor"),
              ev(2, "edit", reviewer="bt2", before="gốc", after="B", track="editor"),
              ev(3, "approve", reviewer="bt2", track="editor"),
              ev(4, "flag", unit="ND254_2026_D10", reviewer="bt1", note="thiếu dòng", track="editor"),
              ev(5, "adopt", reviewer="pd1", before="gốc", after="B", note="Chọn bản của bt2"),
              ev(6, "approve", reviewer="pd1")]
    tracks = fold_tracks(events)
    assert tracks.people == ["bt1", "bt2"]  # theo lần thao tác đầu tiên: màu 1, màu 2
    assert tracks.editors["bt1"][U].edited_content == "A" and tracks.editors["bt1"][U].status == "unreviewed"
    assert tracks.editors["bt1"]["ND254_2026_D10"].status == "flagged"
    assert tracks.editors["bt2"][U].edited_content == "B" and tracks.editors["bt2"][U].status == "approved"
    assert tracks.final[U].edited_content == "B" and tracks.final[U].status == "approved"
    assert "ND254_2026_D10" not in tracks.final  # gắn cờ của bt1 không đụng tới bản chốt
    assert [e.id for e in tracks.events_of("final")] == [5, 6]
    assert [e.id for e in tracks.events_of("bt1")] == [1, 4] and tracks.events_of("ai_do") == []
    assert tracks.reviews_of("bt2") is tracks.editors["bt2"] and tracks.reviews_of("ai_do") == {}


def test_choosing_a_version_changes_the_final_text_so_it_must_be_approved_again():
    reviews = fold_events([ev(1, "approve"), ev(2, "adopt", before="gốc", after="B", note="Chọn bản của bt2")])
    assert reviews[U].status == "unreviewed" and reviews[U].edited_content == "B" and reviews[U].edit_count == 1


def test_role_decides_which_version_an_action_is_written_to():
    assert decide("gốc", [], "edit", "bt1", content="A", role="reviewer")["track"] == "editor"
    assert decide("gốc", [], "flag", "bt1", note="x", role="reviewer")["track"] == "editor"
    assert decide("gốc", [], "approve", "pd1", role="approver")["track"] == "final"
    assert decide("gốc", [], "edit", "admin", content="A", role="admin")["track"] == "final"
    assert "không được sửa chữ" in problems_of("gốc", [], "edit", "pd1", content="A", role="approver")
    assert "không được chọn bản" in problems_of("gốc", [], "adopt", "bt1", role="reviewer", source_content="A")


def test_adopt_copies_the_chosen_version_into_the_final_one():
    fields = decide("gốc", [], "adopt", "pd1", role="approver", source_content="B\r\nhai", note="Chọn bản của bt2")
    assert fields["track"] == "final" and fields["content_before"] == "gốc" and fields["content_after"] == "B\nhai"
    assert fields["note"] == "Chọn bản của bt2"
    prior = [ev(1, "adopt", before="gốc", after="B", note="Chọn bản của bt2")]
    assert "đã giống" in problems_of("gốc", prior, "adopt", "pd1", role="approver", source_content="B")
    assert "đã giống" in problems_of("gốc", [], "adopt", "pd1", role="approver", source_content="gốc")
    assert "chưa chọn bản" in problems_of("gốc", [], "adopt", "pd1", role="approver")
    back = decide("gốc", prior, "adopt", "pd1", role="approver", source_content="gốc", note="Chọn bản OCR gốc")
    assert back["content_before"] == "B" and back["content_after"] == "gốc"


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
    context = dict(doc=doc, unit=unit, unit_label=full_label(unit), can_review=True, review=None, history=[],
                   reviewer="", errors=[], form_note="", form_content=None)
    context.update(extra)
    return env.get_template("partials/detail.html").render(**context)


def test_detail_shows_buttons_and_hides_reset_when_unreviewed(env, doc):
    html = render_detail(env, doc)
    for label in ("Duyệt", "Gắn cờ", "Báo lỗi cấu trúc", "Sửa chữ", "Lưu bản sửa"):
        assert label in html
    assert "single-unit-focus-banner" not in html  # chế độ sửa cũ (chỉ ẩn cây) đã bỏ
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
    assert "31/10/2026 07:45" in html and "lệch với trang 1" in html
    assert 'name="reviewer"' not in html  # người soát là tài khoản đăng nhập, không còn ô tự gõ tên


def test_detail_escapes_user_text_and_keeps_form_values_after_error(env, doc):
    html = render_detail(env, doc, errors=["Cần nhập tên người soát"], form_note="<script>x</script>", form_content="chữ đang gõ dở")
    assert "Cần nhập tên người soát" in html and "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert "chữ đang gõ dở" in html and "data-reopen" in html  # lưu lỗi: mở lại ngay khung sửa tập trung


def test_detail_without_database_has_no_buttons(env, doc):
    html = render_detail(env, doc, can_review=False)
    assert "Duyệt" not in html and "PostgreSQL" in html


def test_detail_without_review_rights_is_read_only(env, doc):
    """Có PostgreSQL nhưng tài khoản không thao tác được: chỉ đọc, không nút, không nhắc PostgreSQL.
    (Tài khoản bị khóa không tới được trang này: test_security.test_locked_account_cannot_sign_in...)"""
    html = render_detail(env, doc, can_review=False, db_ready=True)
    assert "chỉ xem được" in html and 'hx-post' not in html and "PostgreSQL" not in html


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


def test_edit_button_opens_a_focused_editor_with_only_text_and_a_small_toolbar(env, doc):
    html = render_detail(env, doc)
    assert 'onclick="enterFocusEdit()"' in html and "Sửa chữ <kbd>E</kbd></button>" in html
    editor = html[html.index('id="focus-editor"'):]
    editor = editor[:editor.index("</section>")]
    assert 'id="focus-edit-textarea"' in editor and 'name="content"' in editor
    assert 'name="ocr_reason"' in editor and html.count('name="ocr_reason"') == 1  # lý do OCR chỉ hỏi khi sửa chữ
    assert '"action": "edit"' in editor and "Ctrl+Enter" in editor and "Esc" in editor
    assert "Điểm a, Khoản 1, Điều 9" in editor and "data-reopen" not in editor
    assert '<label for="focus-edit-textarea"' in editor  # ô sửa có nhãn cho trình đọc màn hình


def test_review_page_hides_everything_but_the_pdf_and_the_editor_in_focus_mode():
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "style.css").read_text(encoding="utf-8")
    rules = css[css.index("/* CHẾ ĐỘ SỬA TẬP TRUNG"):]
    rules = rules[:rules.index("/* Quy trình trên thanh văn bản")]
    for hidden in (".topbar", ".doc-head", ".review-flash", ".navpane", ".unit-actions",
                   ".unit-body > :not(#review-form)", "#review-form > :not(#focus-editor)"):
        assert f"body.focus-edit {hidden}" in rules
    assert "body.focus-edit .focus-editor" in rules and "editing-single-unit" not in css
    page = (Path(__file__).resolve().parents[1] / "app" / "templates" / "review.html").read_text(encoding="utf-8")
    assert "function enterFocusEdit" in page and "function exitFocusEdit" in page
    assert '"Escape"' in page and "ctrlKey" in page and "data-reopen" in page


def test_templates_have_no_byte_order_mark():
    """BOM ở đầu một partial thành một ký tự lạ trong trang (khung flex còn dành riêng một dòng cho nó)."""
    root = Path(__file__).resolve().parents[1] / "app" / "templates"
    assert [p.name for p in root.rglob("*.html") if p.read_bytes().startswith(b"\xef\xbb\xbf")] == []


# ---- Thứ tự cây và Duyệt & tiếp ----

def test_tree_order_is_depth_first_and_neighbours_follow_it(doc):
    order = tree_order(doc.legal_units)
    assert order == [u.unit_id for u in doc.legal_units]  # dữ liệu mẫu đã xếp cha trước con
    assert order.index("ND254_2026_D9") < order.index("ND254_2026_D9_K1") < order.index("ND254_2026_D9_K1_a")
    assert neighbours(order, order[0]) == (None, order[1])
    assert neighbours(order, order[-1]) == (order[-2], None)
    assert neighbours(order, "không có") == (None, None)


def test_tree_order_puts_units_with_unknown_parent_at_the_root(doc):
    orphan = doc.legal_units[-1].model_copy(update={"unit_id": "X", "parent_unit_id": "MẤT"})
    assert tree_order([*doc.legal_units, orphan])[-1] == "X"


def test_next_unreviewed_skips_reviewed_units_and_wraps_around(doc):
    order = tree_order(doc.legal_units)
    reviews = fold_events([ev(1, "approve", unit=order[1]), ev(2, "flag", unit=order[2], note="sai")])
    assert next_unreviewed(order, order[0], reviews) == order[3]
    everything_after = fold_events([ev(i, "approve", unit=u) for i, u in enumerate(order[4:], 1)])
    assert next_unreviewed(order, order[3], everything_after) == order[0]  # hết cây thì quay lại từ đầu
    done = fold_events([ev(i, "approve", unit=u) for i, u in enumerate(order, 1)])
    assert next_unreviewed(order, order[0], done) is None


def test_full_label_names_point_clause_and_article(doc):
    by_id = {u.unit_id: u for u in doc.legal_units}
    assert full_label(by_id["ND254_2026_D9_K1_a"]) == "Điểm a, Khoản 1, Điều 9"
    assert full_label(by_id["ND254_2026_D9"]) == "Điều 9"


def test_detail_action_bar_has_approve_and_next_with_shortcuts(env, doc):
    order = tree_order(doc.legal_units)
    i = order.index("ND254_2026_D9_K1_a")
    html = render_detail(env, doc, prev_id=order[i - 1], next_id=order[i + 1],
                         prev_url=f"/documents/ND254_2026/units/{order[i - 1]}", next_url=f"/documents/ND254_2026/units/{order[i + 1]}")
    assert '"action": "approve", "advance": "1"' in html and 'data-key="a"' in html
    assert 'data-go="prev"' in html and 'data-go="next"' in html
    assert f'href="/documents/ND254_2026?unit={order[i + 1]}"' in html  # Tiếp vẫn là liên kết thường
    reason = html[html.index('id="reason-row"'):]
    assert reason.startswith('id="reason-row" hidden') and 'name="note"' in reason  # ô lý do chỉ mở khi Gắn cờ


def test_detail_reopens_reason_after_missing_note(env, doc):
    html = render_detail(env, doc, errors=["Cần ghi lý do"], reason_open="structure_error")
    reason = html[html.index('id="reason-row"'):]
    assert not reason.startswith('id="reason-row" hidden') and "Lỗi cấu trúc ở đâu" in reason
