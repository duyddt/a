"""Thống kê tiến độ cho Admin (trang /admin/stats, GET /api/stats). Thuần Python: nhận dữ liệu đã đọc từ DB.

- **Mỗi người:**
  - người chỉnh sửa: số văn bản được giao, số văn bản đã bấm Hoàn thành (hoặc đã được phê duyệt), số đơn vị đã xử lý
    trên bản riêng của mình (duyệt / gắn cờ / lỗi cấu trúc);
  - người phê duyệt: số văn bản được giao, số văn bản đã phê duyệt xong, số văn bản đang chờ mình, số đơn vị đã duyệt
    trên bản chốt.

  % = đã hoàn thành / được giao.
- **Mỗi văn bản:** trạng thái quy trình, tiến độ của từng người chỉnh sửa (theo màu của họ trong văn bản), người phê
  duyệt và số đơn vị đã duyệt trên bản chốt.
- **Tổng quan:** số văn bản theo từng trạng thái.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from .accounts import ROLE_LABELS
from .models import PERSON_COLORS
from .review import EDITOR, FINAL, ReviewEvent, fold_events
from .workflow import STATUSES, Workflow, WorkflowEvent

DONE_STATUSES = ("approved", "flagged", "structure_error")  # đơn vị đã có kết luận


def _percent(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


@dataclass
class PersonStats:
    username: str
    display_name: str
    role: str
    assigned: int = 0
    completed: int = 0
    waiting: int = 0  # người phê duyệt: văn bản đang chờ mình phê duyệt
    units_done: int = 0
    units_total: int = 0
    last_activity: datetime | None = None

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def percent(self) -> int:
        return _percent(self.completed, self.assigned)

    @property
    def units_percent(self) -> int:
        return _percent(self.units_done, self.units_total)


@dataclass
class DocumentStats:
    document_id: str
    title: str | None
    categories: list[str]
    status: str
    status_label: str
    unit_count: int
    final_approved: int
    approver: str | None
    editors: list[dict] = field(default_factory=list)  # username, color, submitted, units_done, percent

    @property
    def final_percent(self) -> int:
        return _percent(self.final_approved, self.unit_count)


@dataclass
class Stats:
    people: list[PersonStats]
    documents: list[DocumentStats]
    status_counts: dict[str, int]

    @property
    def total_documents(self) -> int:
        return len(self.documents)


def _count_done(reviews: dict, statuses: tuple[str, ...]) -> int:
    return sum(1 for state in reviews.values() if state.status in statuses)


def build_stats(
    documents: Iterable,
    events_by_document: dict[str, list[ReviewEvent]],
    workflows: dict[str, Workflow],
    accounts: Iterable[dict],
    workflow_events: Iterable[WorkflowEvent],
) -> Stats:
    """`documents`: DocumentSummary (đã lọc theo thư mục nếu cần); `events_by_document`: sự kiện thẩm định của các đơn vị
    còn tồn tại; `workflows`: trạng thái quy trình (văn bản không có = chưa phân công)."""
    documents = list(documents)
    shown = {d.document_id for d in documents}
    people = {a["username"]: PersonStats(a["username"], a.get("display_name") or a["username"], a["role"]) for a in accounts}

    def touch(name: str, when: datetime | None) -> None:
        person = people.get(name)
        if person is not None and when is not None and (person.last_activity is None or when > person.last_activity):
            person.last_activity = when

    for event in workflow_events:
        if event.document_id in shown:
            touch(event.actor, event.created_at)

    doc_stats: list[DocumentStats] = []
    counts = dict.fromkeys(STATUSES, 0)
    for doc in documents:
        events = events_by_document.get(doc.document_id, [])
        for event in events:
            touch(event.reviewer, event.created_at)
        state = workflows.get(doc.document_id) or Workflow(document_id=doc.document_id)
        units = doc.unit_count or 0
        final = fold_events(e for e in events if e.track == FINAL)
        final_approved = _count_done(final, ("approved",))
        counts[state.status] += 1

        editors = []
        for index, (name, submitted) in enumerate(state.editors.items()):
            own = fold_events(e for e in events if e.track == EDITOR and e.reviewer == name)
            done = _count_done(own, DONE_STATUSES)
            editors.append({"username": name, "color": index % PERSON_COLORS + 1, "submitted": submitted,
                            "units_done": done, "percent": _percent(done, units)})
            person = people.get(name)
            if person is not None:
                person.assigned += 1
                person.completed += 1 if submitted or state.status == "approved" else 0
                person.units_done += done
                person.units_total += units
        if state.approver and state.approver in people:
            person = people[state.approver]
            person.assigned += 1
            person.completed += 1 if state.status == "approved" else 0
            person.waiting += 1 if state.status == "pending" else 0
            person.units_done += final_approved
            person.units_total += units
        doc_stats.append(DocumentStats(
            document_id=doc.document_id, title=doc.title, categories=sorted(doc.categories or {}),
            status=state.status, status_label=state.status_label, unit_count=units,
            final_approved=final_approved, approver=state.approver, editors=editors,
        ))

    order = {"reviewer": 0, "approver": 1, "admin": 2}
    listed = sorted((p for p in people.values() if p.assigned), key=lambda p: (order.get(p.role, 9), p.username))
    return Stats(people=listed, documents=doc_stats, status_counts=counts)
