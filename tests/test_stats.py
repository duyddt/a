"""Thống kê tiến độ cho Admin (app/services/stats.py): thuần Python, không cần DB."""
from datetime import datetime, timedelta

from app.services.models import DocumentSummary
from app.services.review import ReviewEvent
from app.services.stats import build_stats
from app.services.workflow import WorkflowEvent, fold_workflows

T0 = datetime(2026, 11, 17, 8, 0)
_ids = iter(range(1, 10_000))


def rev(unit, action, reviewer, track="editor", minutes=0):
    return ReviewEvent(next(_ids), unit, action, reviewer, created_at=T0 + timedelta(minutes=minutes), track=track,
                       content_after="x" if action in ("edit", "adopt") else None)


def wf(document_id, action, actor="admin", target_user=None, target_role=None, minutes=0, note=None):
    return WorkflowEvent(next(_ids), document_id, action, actor, target_user, target_role, note,
                         T0 + timedelta(minutes=minutes))


ACCOUNTS = [
    {"username": "bt1", "display_name": "Bình", "role": "reviewer", "is_approved": True},
    {"username": "bt2", "display_name": "bt2", "role": "reviewer", "is_approved": True},
    {"username": "pd1", "display_name": "Phúc", "role": "approver", "is_approved": True},
    {"username": "admin", "display_name": "admin", "role": "admin", "is_approved": True},
    {"username": "cu", "display_name": "cu", "role": "reviewer", "is_approved": False},
]
DOCS = [
    DocumentSummary("A", title="Văn bản A", unit_count=4, categories={"I": 1}),
    DocumentSummary("B", title="Văn bản B", unit_count=2, categories={"II": 1}),
    DocumentSummary("C", title="Văn bản C", unit_count=3, categories={"I": 2}),
]


def scenario():
    flow = [
        wf("A", "assign", target_user="bt1", target_role="editor"), wf("A", "assign", target_user="bt2", target_role="editor"),
        wf("A", "assign", target_user="pd1", target_role="approver"),
        wf("B", "assign", target_user="bt1", target_role="editor"), wf("B", "assign", target_user="pd1", target_role="approver"),
        wf("A", "submit", "bt1", minutes=30), wf("A", "submit", "bt2", minutes=31),
        wf("B", "submit", "bt1", minutes=40), wf("B", "finalize", "pd1", minutes=50),
    ]
    events = {
        "A": [rev("A1", "approve", "bt1", minutes=1), rev("A2", "flag", "bt1", minutes=2), rev("A3", "edit", "bt1", minutes=3),
              rev("A1", "approve", "bt2", minutes=5), rev("A1", "approve", "pd1", "final", minutes=45)],
        "B": [rev("B1", "approve", "bt1", minutes=35), rev("B2", "approve", "bt1", minutes=36),
              rev("B1", "approve", "pd1", "final", minutes=46), rev("B2", "approve", "pd1", "final", minutes=47)],
    }
    return build_stats(DOCS, events, fold_workflows(flow), ACCOUNTS, flow)


def test_each_person_has_assigned_finished_and_percent_of_documents():
    people = {p.username: p for p in scenario().people}
    assert set(people) == {"bt1", "bt2", "pd1"}  # Admin và tài khoản bị khóa không có việc thì không liệt kê
    bt1, bt2, pd1 = people["bt1"], people["bt2"], people["pd1"]
    assert (bt1.assigned, bt1.completed, bt1.percent) == (2, 2, 100)
    assert (bt2.assigned, bt2.completed, bt2.percent) == (1, 1, 100)
    # đơn vị đã xử lý (duyệt / gắn cờ / lỗi cấu trúc) trên bản riêng, trong các văn bản được giao
    assert (bt1.units_done, bt1.units_total) == (4, 6) and (bt2.units_done, bt2.units_total) == (1, 4)
    assert (pd1.assigned, pd1.completed, pd1.waiting, pd1.percent) == (2, 1, 1, 50)
    assert (pd1.units_done, pd1.units_total) == (3, 6)  # đơn vị đã duyệt trên bản chốt
    assert bt1.last_activity == T0 + timedelta(minutes=40) and pd1.last_activity == T0 + timedelta(minutes=50)
    assert bt1.display_name == "Bình" and bt1.role_label == "Người chỉnh sửa"


def test_each_document_shows_its_status_people_and_final_progress():
    docs = {d.document_id: d for d in scenario().documents}
    a = docs["A"]
    assert a.status == "pending" and a.approver == "pd1" and (a.final_approved, a.unit_count) == (1, 4)
    assert [(e["username"], e["color"], e["submitted"], e["units_done"]) for e in a.editors] == [
        ("bt1", 1, True, 2), ("bt2", 2, True, 1)]
    assert docs["B"].status == "approved" and docs["B"].final_percent == 100
    assert docs["C"].status == "unassigned" and docs["C"].editors == []


def test_overview_counts_documents_by_status_and_can_be_filtered_by_folder():
    stats = scenario()
    assert stats.status_counts == {"unassigned": 1, "editing": 0, "returned": 0, "pending": 1, "approved": 1}
    assert stats.total_documents == 3
    only_i = build_stats([d for d in DOCS if "I" in d.categories], {}, {}, ACCOUNTS, [])
    assert only_i.total_documents == 2 and only_i.status_counts["unassigned"] == 2 and only_i.people == []
