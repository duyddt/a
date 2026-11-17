"""Quy trình làm việc của một văn bản: Admin phân công → người chỉnh sửa Hoàn thành → người phê duyệt Trả lại hoặc
Hoàn tất phê duyệt.

Giống review.py: lịch sử `workflow_event` chỉ ghi thêm, trạng thái luôn tính lại từ lịch sử (`fold_workflow`), còn
`decide_workflow` kiểm tra một thao tác mới trước khi ghi. Không phụ thuộc DB nên test được bằng dữ liệu giả.

Trạng thái văn bản:
- `unassigned` Chưa phân công: chưa giao cho người chỉnh sửa nào;
- `editing` Đang chỉnh sửa: còn người chỉnh sửa chưa bấm Hoàn thành;
- `returned` Trả lại: người phê duyệt trả lại kèm ghi chú, chờ người chỉnh sửa nộp lại;
- `pending` Chờ phê duyệt: mọi người chỉnh sửa đã bấm Hoàn thành (văn bản chuyển sang người phê duyệt ngay, không chờ
  các văn bản khác);
- `approved` Đã phê duyệt.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from .review import ReviewProblem

WORKFLOW_ACTIONS = ("assign", "unassign", "submit", "reopen", "return", "finalize")
EDITOR, APPROVER = "editor", "approver"  # target_role: vai trò trong văn bản
STATUSES = ("unassigned", "editing", "returned", "pending", "approved")
STATUS_LABELS = {
    "unassigned": "Chưa phân công",
    "editing": "Đang chỉnh sửa",
    "returned": "Trả lại",
    "pending": "Chờ phê duyệt",
    "approved": "Đã phê duyệt",
}
ACTION_LABELS = {
    "assign": "Phân công",
    "unassign": "Gỡ phân công",
    "submit": "Hoàn thành",
    "reopen": "Mở lại",
    "return": "Trả lại",
    "finalize": "Hoàn tất phê duyệt",
}
# vai trò tài khoản được nhận từng vai trò trong văn bản
_ACCOUNT_ROLE_FOR = {EDITOR: "reviewer", APPROVER: "approver"}
_ROLE_WORDS = {EDITOR: "người chỉnh sửa", APPROVER: "người phê duyệt"}


@dataclass
class WorkflowEvent:
    id: int
    document_id: str
    action: str
    actor: str
    target_user: str | None = None
    target_role: str | None = None
    note: str | None = None
    created_at: datetime | None = None


@dataclass
class Workflow:
    """Trạng thái quy trình của một văn bản, tính từ lịch sử."""

    document_id: str = ""
    editors: dict[str, bool] = field(default_factory=dict)  # người chỉnh sửa (theo thứ tự giao) -> đã bấm Hoàn thành
    approver: str | None = None  # None: mọi người phê duyệt đều nhận được văn bản
    finalized: bool = False
    finalized_by: str | None = None
    finalized_at: datetime | None = None
    returned_note: str | None = None  # ghi chú trả lại, còn hiệu lực đến khi mọi người nộp lại
    returned_by: str | None = None
    submitted_at: dict[str, datetime | None] = field(default_factory=dict)
    updated_at: datetime | None = None

    @property
    def status(self) -> str:
        if self.finalized:
            return "approved"
        if not self.editors:
            return "unassigned"
        if all(self.editors.values()):
            return "pending"
        return "returned" if self.returned_note else "editing"

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    def is_editor(self, username: str) -> bool:
        return username in self.editors

    def has_submitted(self, username: str) -> bool:
        return bool(self.editors.get(username))

    def is_approver_for(self, username: str) -> bool:
        return self.approver is None or self.approver == username


def fold_workflow(events: Iterable[WorkflowEvent], document_id: str = "") -> Workflow:
    """Trạng thái quy trình sau khi áp lần lượt các sự kiện (đã xếp theo id tăng dần)."""
    state = Workflow(document_id=document_id)
    for event in events:
        state.document_id = state.document_id or event.document_id
        state.updated_at = event.created_at or state.updated_at
        if event.action == "assign":
            if event.target_role == EDITOR:
                state.editors.setdefault(event.target_user, False)
            else:
                state.approver = event.target_user
        elif event.action == "unassign":
            if event.target_role == EDITOR:
                state.editors.pop(event.target_user, None)
                state.submitted_at.pop(event.target_user, None)
            elif state.approver == event.target_user:
                state.approver = None
        elif event.action == "submit":
            if event.actor in state.editors:
                state.editors[event.actor] = True
                state.submitted_at[event.actor] = event.created_at
        elif event.action == "reopen":
            if event.target_user:
                if event.target_user in state.editors:
                    state.editors[event.target_user] = False
                    state.submitted_at.pop(event.target_user, None)
            else:
                state.finalized, state.finalized_by, state.finalized_at = False, None, None
        elif event.action == "return":
            state.editors = dict.fromkeys(state.editors, False)
            state.submitted_at.clear()
            state.returned_note, state.returned_by = event.note, event.actor
            state.finalized = False
        elif event.action == "finalize":
            state.finalized, state.finalized_by, state.finalized_at = True, event.actor, event.created_at
        if state.editors and all(state.editors.values()):
            state.returned_note = state.returned_by = None  # mọi người đã nộp lại: ghi chú trả lại hết hiệu lực
    return state


def fold_workflows(events: Iterable[WorkflowEvent]) -> dict[str, Workflow]:
    """document_id -> trạng thái quy trình, cho mọi văn bản có sự kiện."""
    grouped: dict[str, list[WorkflowEvent]] = defaultdict(list)
    for event in events:
        grouped[event.document_id].append(event)
    return {document_id: fold_workflow(evs, document_id) for document_id, evs in grouped.items()}


def can_act_problem(state: Workflow | None, user: dict) -> str | None:
    """Lý do `user` chưa được thao tác thẩm định trên văn bản ở trạng thái `state`; None nếu được.

    Người chỉnh sửa: phải được giao và chưa bấm Hoàn thành. Người phê duyệt: văn bản đã chuyển sang (Chờ phê duyệt) và
    giao cho mình hoặc chưa giao cho ai. Admin: luôn được."""
    state = state or Workflow()
    role, name = user.get("role"), user.get("username")
    if role == "admin":
        return None
    if state.status == "approved":
        return "Văn bản đã được phê duyệt; cần Admin mở lại để thao tác tiếp"
    if role == "reviewer":
        if not state.is_editor(name):
            return "Bạn chưa được giao chỉnh sửa văn bản này"
        if state.has_submitted(name):
            return "Bạn đã bấm Hoàn thành văn bản này; chờ người phê duyệt trả lại hoặc Admin mở lại để sửa tiếp"
        return None
    if role == "approver":
        if not state.is_approver_for(name):
            return f"Văn bản được giao cho người phê duyệt khác ({state.approver})"
        if state.status != "pending":
            return "Văn bản chưa chuyển sang phê duyệt: người chỉnh sửa chưa bấm Hoàn thành"
        return None
    return "Tài khoản không có quyền thao tác"


def decide_workflow(
    state: Workflow,
    action: str,
    user: dict,
    note: str | None = None,
    target_user: str | None = None,
    target_role: str | None = None,
    target_account_role: str | None = None,
    unapproved: int = 0,
) -> dict:
    """Kiểm tra một thao tác quy trình; trả các trường cần ghi hoặc ném ReviewProblem.

    `target_account_role`: vai trò tài khoản của `target_user` (None = không có tài khoản đó).
    `unapproved`: số đơn vị trên bản chốt chưa duyệt (cho `finalize`)."""
    note = (note or "").strip() or None
    target_user = (target_user or "").strip() or None
    role, name = user.get("role"), user.get("username")
    fields = {"action": action, "actor": name, "target_user": None, "target_role": None, "note": note}
    if action not in WORKFLOW_ACTIONS:
        raise ReviewProblem([f"Không có thao tác quy trình '{action}'"])

    if action in ("assign", "unassign", "reopen"):
        if role != "admin":
            raise ReviewProblem([f"Chỉ Admin được {ACTION_LABELS[action].lower()}"])
        if action == "reopen":
            if target_user:
                if not state.has_submitted(target_user):
                    raise ReviewProblem([f"{target_user} chưa bấm Hoàn thành văn bản này"])
                return {**fields, "target_user": target_user, "target_role": EDITOR}
            if state.status != "approved":
                raise ReviewProblem(["Văn bản chưa được phê duyệt nên không cần mở lại"])
            return fields
        if target_role not in (EDITOR, APPROVER) or not target_user:
            raise ReviewProblem(["Thiếu người được giao hoặc vai trò (người chỉnh sửa / người phê duyệt)"])
        if action == "unassign":
            assigned = state.is_editor(target_user) if target_role == EDITOR else state.approver == target_user
            if not assigned:
                raise ReviewProblem([f"{target_user} không được giao làm {_ROLE_WORDS[target_role]} văn bản này"])
            return {**fields, "target_user": target_user, "target_role": target_role}
        if target_account_role is None:
            raise ReviewProblem([f"Không có tài khoản {target_user}"])
        if target_account_role != _ACCOUNT_ROLE_FOR[target_role]:
            raise ReviewProblem([f"{target_user} không phải {_ROLE_WORDS[target_role]}"])
        already = state.is_editor(target_user) if target_role == EDITOR else state.approver == target_user
        if already:
            raise ReviewProblem([f"{target_user} đã được giao văn bản này"])
        return {**fields, "target_user": target_user, "target_role": target_role}

    if action == "submit":
        if role != "reviewer":
            raise ReviewProblem(["Chỉ người chỉnh sửa bấm Hoàn thành"])
        if state.status == "approved":
            raise ReviewProblem(["Văn bản đã được phê duyệt"])
        if not state.is_editor(name):
            raise ReviewProblem(["Bạn chưa được giao chỉnh sửa văn bản này"])
        if state.has_submitted(name):
            raise ReviewProblem(["Bạn đã bấm Hoàn thành văn bản này rồi"])
        return fields

    # return / finalize: người phê duyệt được giao (hoặc chưa giao ai) và Admin
    if role not in ("approver", "admin"):
        raise ReviewProblem([f"Người chỉnh sửa không được {ACTION_LABELS[action].lower()}"])
    if state.status == "approved":
        raise ReviewProblem(["Văn bản đã được phê duyệt"])
    if role == "approver":
        if not state.is_approver_for(name):
            raise ReviewProblem([f"Văn bản được giao cho người phê duyệt khác ({state.approver})"])
        if state.status != "pending":
            raise ReviewProblem(["Văn bản chưa chuyển sang phê duyệt: người chỉnh sửa chưa bấm Hoàn thành"])
    if action == "return":
        if not state.editors:
            raise ReviewProblem(["Văn bản chưa giao cho người chỉnh sửa nào để trả lại"])
        if role == "admin" and state.status != "pending":
            raise ReviewProblem(["Văn bản chưa chuyển sang phê duyệt: người chỉnh sửa chưa bấm Hoàn thành"])
        if not note:
            raise ReviewProblem(["Trả lại cần ghi chú chỗ còn sai sót"])
        return fields
    if unapproved:
        raise ReviewProblem([f"Còn {unapproved} đơn vị trên bản chốt chưa duyệt"])
    return fields


def available_actions(state: Workflow | None, user: dict | None) -> dict[str, bool]:
    """Nút quy trình hiện cho `user` trên trang văn bản (server vẫn kiểm tra lại bằng `decide_workflow`)."""
    if state is None or not user or not user.get("is_approved"):
        return {}
    role, name, status = user.get("role"), user.get("username"), state.status
    approver_here = role == "admin" or (role == "approver" and state.is_approver_for(name))
    return {
        "submit": role == "reviewer" and state.is_editor(name) and not state.has_submitted(name) and status != "approved",
        "return": approver_here and status == "pending",
        "finalize": approver_here and status != "approved" and (role == "admin" or status == "pending"),
        "reopen": role == "admin" and status == "approved",
    }
