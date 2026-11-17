"""Quy trình phân công → hoàn thành → phê duyệt (app/services/workflow.py): thuần Python, không cần DB."""
import pytest
from app.services.review import ReviewProblem
from app.services.workflow import (
    WorkflowEvent,
    can_act_problem,
    decide_workflow,
    fold_workflow,
    fold_workflows,
)

ADMIN = {"username": "admin", "role": "admin", "is_approved": True}
BT1 = {"username": "bt1", "role": "reviewer", "is_approved": True}
BT2 = {"username": "bt2", "role": "reviewer", "is_approved": True}
PD1 = {"username": "pd1", "role": "approver", "is_approved": True}
PD2 = {"username": "pd2", "role": "approver", "is_approved": True}

_ids = iter(range(1, 10_000))


def ev(action, actor="admin", target_user=None, target_role=None, note=None, document_id="D"):
    return WorkflowEvent(next(_ids), document_id, action, actor, target_user, target_role, note)


def assigned(*extra):
    return [ev("assign", target_user="bt1", target_role="editor"), ev("assign", target_user="bt2", target_role="editor"),
            ev("assign", target_user="pd1", target_role="approver"), *extra]


def problem(fn, *args, **kwargs):
    with pytest.raises(ReviewProblem) as exc:
        fn(*args, **kwargs)
    return " ".join(exc.value.problems)


def test_a_document_moves_from_editing_to_waiting_for_approval_once_every_editor_has_finished():
    assert fold_workflow([]).status == "unassigned"
    state = fold_workflow(assigned())
    assert state.status == "editing" and list(state.editors) == ["bt1", "bt2"] and state.approver == "pd1"
    state = fold_workflow(assigned(ev("submit", "bt1")))
    assert state.status == "editing" and state.editors == {"bt1": True, "bt2": False}
    state = fold_workflow(assigned(ev("submit", "bt1"), ev("submit", "bt2")))
    assert state.status == "pending" and state.status_label == "Chờ phê duyệt"
    assert fold_workflow(assigned(ev("submit", "bt1"), ev("submit", "bt2"), ev("finalize", "pd1"))).status == "approved"


def test_returning_sends_the_document_back_to_every_editor_with_the_note():
    events = assigned(ev("submit", "bt1"), ev("submit", "bt2"), ev("return", "pd1", note="Điều 3 còn sai dấu"))
    state = fold_workflow(events)
    assert state.status == "returned" and state.editors == {"bt1": False, "bt2": False}
    assert state.returned_note == "Điều 3 còn sai dấu" and state.returned_by == "pd1"
    state = fold_workflow([*events, ev("submit", "bt1"), ev("submit", "bt2")])
    assert state.status == "pending" and state.returned_note is None


def test_admin_can_unassign_and_reopen():
    events = assigned(ev("submit", "bt1"), ev("unassign", target_user="bt2", target_role="editor"))
    assert fold_workflow(events).status == "pending"  # chỉ còn bt1, đã nộp
    events.append(ev("reopen", target_user="bt1"))
    assert fold_workflow(events).status == "editing"
    done = assigned(ev("submit", "bt1"), ev("submit", "bt2"), ev("finalize", "pd1"), ev("reopen"))
    assert fold_workflow(done).status == "pending"  # mở lại văn bản đã phê duyệt: về chờ phê duyệt
    other = assigned(ev("assign", target_user="pd2", target_role="approver"))
    assert fold_workflow(other).approver == "pd2"  # mỗi văn bản một người phê duyệt: giao người mới thay người cũ


def test_fold_workflows_groups_events_by_document():
    states = fold_workflows([ev("assign", target_user="bt1", target_role="editor", document_id="A"),
                             ev("assign", target_user="bt2", target_role="editor", document_id="B")])
    assert sorted(states) == ["A", "B"] and list(states["B"].editors) == ["bt2"]


def test_only_assigned_editors_who_have_not_finished_may_work_on_their_version():
    state = fold_workflow(assigned())
    assert can_act_problem(state, BT1) is None
    assert "chưa được giao" in can_act_problem(fold_workflow([]), BT1)
    assert "chưa được giao" in can_act_problem(state, {"username": "bt9", "role": "reviewer"})
    submitted = fold_workflow(assigned(ev("submit", "bt1")))
    assert "Hoàn thành" in can_act_problem(submitted, BT1) and can_act_problem(submitted, BT2) is None


def test_the_approver_works_only_once_the_document_has_been_handed_over():
    assert "chưa chuyển sang phê duyệt" in can_act_problem(fold_workflow(assigned(ev("submit", "bt1"))), PD1)
    pending = fold_workflow(assigned(ev("submit", "bt1"), ev("submit", "bt2")))
    assert can_act_problem(pending, PD1) is None
    assert "người phê duyệt khác" in can_act_problem(pending, PD2)
    unassigned_approver = fold_workflow([ev("assign", target_user="bt1", target_role="editor"), ev("submit", "bt1")])
    assert can_act_problem(unassigned_approver, PD2) is None  # chưa giao người phê duyệt: ai cũng nhận được
    approved = fold_workflow(assigned(ev("submit", "bt1"), ev("submit", "bt2"), ev("finalize", "pd1")))
    assert "đã được phê duyệt" in can_act_problem(approved, PD1)
    assert can_act_problem(fold_workflow([]), ADMIN) is None and can_act_problem(approved, ADMIN) is None  # Admin toàn quyền


def test_workflow_actions_are_checked_against_role_and_state():
    state = fold_workflow(assigned())
    assert decide_workflow(state, "submit", BT1)["actor"] == "bt1"
    assert "chưa được giao" in problem(decide_workflow, fold_workflow([]), "submit", BT1)
    assert "đã bấm Hoàn thành" in problem(decide_workflow, fold_workflow(assigned(ev("submit", "bt1"))), "submit", BT1)
    assert "Chỉ Admin" in problem(decide_workflow, state, "assign", PD1, target_user="bt9", target_role="editor",
                                  target_account_role="reviewer")
    assert "người chỉnh sửa" in problem(decide_workflow, state, "assign", ADMIN, target_user="pd2", target_role="editor",
                                        target_account_role="approver")
    assert "đã được giao" in problem(decide_workflow, state, "assign", ADMIN, target_user="bt1", target_role="editor",
                                     target_account_role="reviewer")
    assert "Không có tài khoản" in problem(decide_workflow, state, "assign", ADMIN, target_user="ma", target_role="editor",
                                           target_account_role=None)
    fields = decide_workflow(state, "assign", ADMIN, target_user="pd2", target_role="approver", target_account_role="approver")
    assert (fields["target_user"], fields["target_role"]) == ("pd2", "approver")
    assert "không được giao" in problem(decide_workflow, state, "unassign", ADMIN, target_user="bt9", target_role="editor")


def test_return_needs_a_note_and_finalize_needs_every_unit_approved_on_the_final_version():
    pending = fold_workflow(assigned(ev("submit", "bt1"), ev("submit", "bt2")))
    assert "ghi chú" in problem(decide_workflow, pending, "return", PD1, note="  ")
    assert decide_workflow(pending, "return", PD1, note="Điều 3 sai")["note"] == "Điều 3 sai"
    assert "chưa chuyển sang phê duyệt" in problem(decide_workflow, fold_workflow(assigned()), "return", PD1, note="x")
    assert "Còn 3 đơn vị" in problem(decide_workflow, pending, "finalize", PD1, unapproved=3)
    assert decide_workflow(pending, "finalize", PD1, unapproved=0)["action"] == "finalize"
    assert "người phê duyệt khác" in problem(decide_workflow, pending, "finalize", PD2, unapproved=0)
    assert "Người chỉnh sửa" in problem(decide_workflow, pending, "finalize", BT1, unapproved=0)
    # Admin hoàn tất được cả văn bản chưa phân công, miễn bản chốt đã duyệt hết
    assert decide_workflow(fold_workflow([]), "finalize", ADMIN, unapproved=0)["action"] == "finalize"
    approved = fold_workflow(assigned(ev("submit", "bt1"), ev("submit", "bt2"), ev("finalize", "pd1")))
    assert "đã được phê duyệt" in problem(decide_workflow, approved, "finalize", ADMIN, unapproved=0)
    assert decide_workflow(approved, "reopen", ADMIN)["target_user"] is None
    assert "chưa bấm Hoàn thành" in problem(decide_workflow, pending.__class__(**{**pending.__dict__, "editors": {"bt1": False}}),
                                            "reopen", ADMIN, target_user="bt1")
    assert "không có" in problem(decide_workflow, pending, "frobnicate", ADMIN).lower()


def test_buttons_follow_role_and_state():
    from app.services.workflow import available_actions

    editing = fold_workflow(assigned())
    pending = fold_workflow(assigned(ev("submit", "bt1"), ev("submit", "bt2")))
    approved = fold_workflow(assigned(ev("submit", "bt1"), ev("submit", "bt2"), ev("finalize", "pd1")))

    def on(state, user):
        return sorted(k for k, v in available_actions(state, user).items() if v)

    assert on(editing, BT1) == ["submit"] and on(editing, PD1) == [] and on(editing, ADMIN) == ["finalize"]
    assert on(pending, BT1) == [] and on(pending, PD1) == ["finalize", "return"] and on(pending, PD2) == []
    assert on(approved, ADMIN) == ["reopen"] and on(approved, PD1) == []
    assert available_actions(pending, {**PD1, "is_approved": False}) == {}  # tài khoản bị khóa
