"""Kiểm thử toàn diện 4 thao tác thẩm định, lưu trữ append-only, tính đồng thời và xuất JSON."""
import io
import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.contracts import LegalDocument
from app.db.models import Base, DecisionType, ReviewDecision, StructuralErrorType, UnitRevision, User, UserRole
from app.services.review_service import (
    approve_unit,
    edit_unit_text,
    export_parser_feedback,
    export_verified_document,
    flag_unit,
    get_all_unit_statuses,
    get_document_progress,
    get_unit_history,
    get_unit_status,
    report_structural_error,
)
from app.services.store import DocumentStore

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db_session():
    """Tạo DB SQLite in-memory riêng cho mỗi test case."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        # Seed users
        user1 = User(id=1, username="duy.nguyen", full_name="Nguyễn Đức Duy", role=UserRole.REVIEWER)
        user2 = User(id=2, username="dung.nguyen", full_name="Nguyễn Đăng Hùng Dũng", role=UserRole.REVIEWER)
        session.add_all([user1, user2])
        session.commit()

        yield session


@pytest.fixture
def store():
    return DocumentStore(ROOT / "data")


def test_approve_operation(db_session, store):
    """Thao tác 1: Duyệt đơn vị."""
    doc_id = "ND254_2026"
    unit_id = "ND254_2026_D1"

    decision = approve_unit(db_session, doc_id, unit_id, user_id=1)
    assert decision.decision == DecisionType.APPROVED
    assert decision.user_id == 1

    # Kiểm tra trạng thái
    status = get_unit_status(db_session, doc_id, unit_id, "Nội dung gốc Điều 1")
    assert status.decision == DecisionType.APPROVED
    assert status.reviewer_name == "Nguyễn Đức Duy"


def test_edit_text_append_only(db_session, store):
    """Thao tác 2: Sửa chữ (Append-only: không ghi đè dữ liệu gốc, lưu toàn bộ lịch sử)."""
    doc_id = "ND255_2026"
    unit_id = "ND255_2026_D1"
    original_text = "Nội dung gốc ban đầu"

    # Sửa lần 1 bởi Duy
    rev1 = edit_unit_text(db_session, doc_id, unit_id, user_id=1, new_content="Nội dung đã sửa lần 1", original_content=original_text)
    assert rev1.content_before == original_text
    assert rev1.content_after == "Nội dung đã sửa lần 1"

    # Sửa lần 2 bởi Dũng
    rev2 = edit_unit_text(db_session, doc_id, unit_id, user_id=2, new_content="Nội dung đã sửa lần 2 (chuẩn)", original_content=original_text)
    assert rev2.content_before == "Nội dung đã sửa lần 1"
    assert rev2.content_after == "Nội dung đã sửa lần 2 (chuẩn)"

    # Tổng số bản ghi revision phải là 2 (chỉ INSERT, không UPDATE/DELETE)
    all_revs = db_session.query(UnitRevision).filter_by(document_id=doc_id, unit_id=unit_id).all()
    assert len(all_revs) == 2

    # Trạng thái hiện tại phải trả về bản sửa mới nhất
    status = get_unit_status(db_session, doc_id, unit_id, original_text)
    assert status.is_edited is True
    assert status.revision_count == 2
    assert status.current_content == "Nội dung đã sửa lần 2 (chuẩn)"
    assert status.original_content == original_text  # Bản gốc vẫn nguyên vẹn

    # Lấy lịch sử chi tiết
    history = get_unit_history(db_session, doc_id, unit_id, original_text)
    assert len(history["revisions"]) == 2
    assert history["revisions"][0]["reviewer"] == "Nguyễn Đức Duy"
    assert history["revisions"][1]["reviewer"] == "Nguyễn Đăng Hùng Dũng"
    assert history["original_content"] == original_text


def test_flag_operation(db_session, store):
    """Thao tác 3: Gắn cờ không chắc chắn."""
    doc_id = "ND254_2026"
    unit_id = "ND254_2026_D2"

    flag = flag_unit(db_session, doc_id, unit_id, user_id=1, note="Chữ mờ ở cuối trang 2")
    assert flag.decision == DecisionType.FLAGGED
    assert flag.note == "Chữ mờ ở cuối trang 2"

    status = get_unit_status(db_session, doc_id, unit_id, "Nội dung Điều 2")
    assert status.decision == DecisionType.FLAGGED
    assert status.note == "Chữ mờ ở cuối trang 2"


def test_structural_error_operation(db_session, store):
    """Thao tác 4: Báo lỗi cấu trúc."""
    doc_id = "ND254_2026"
    unit_id = "ND254_2026_D3"

    err = report_structural_error(
        db_session,
        doc_id,
        unit_id,
        user_id=2,
        error_type=StructuralErrorType.NEED_SPLIT,
        note="Điểm a bị dính liền với Điểm b, cần parser tách riêng",
    )
    assert err.decision == DecisionType.STRUCTURAL_ERROR
    assert err.structural_error_type == StructuralErrorType.NEED_SPLIT

    status = get_unit_status(db_session, doc_id, unit_id, "Nội dung Điều 3")
    assert status.decision == DecisionType.STRUCTURAL_ERROR
    assert status.structural_error_type == StructuralErrorType.NEED_SPLIT
    assert status.note == "Điểm a bị dính liền với Điểm b, cần parser tách riêng"


def test_concurrency_two_reviewers_two_documents(db_session, store):
    """Tiêu chuẩn: Hai người soát hai văn bản khác nhau cùng lúc không ảnh hưởng nhau."""
    doc1 = "ND254_2026"
    doc2 = "ND255_2026"

    # Duy duyệt ND254
    approve_unit(db_session, doc1, "ND254_2026_D1", user_id=1)
    approve_unit(db_session, doc1, "ND254_2026_D2", user_id=1)

    # Dũng sửa chữ và gắn cờ ND255
    edit_unit_text(db_session, doc2, "ND255_2026_D1", user_id=2, new_content="Sửa Điều 1 ND255", original_content="Gốc")
    flag_unit(db_session, doc2, "ND255_2026_D2", user_id=2, note="Cờ ND255")

    # Kiểm tra tiến độ độc lập hoàn toàn
    p1 = get_document_progress(db_session, doc1, total_units=10)
    p2 = get_document_progress(db_session, doc2, total_units=10)

    assert p1["approved_units"] == 2
    assert p1["flagged_units"] == 0
    assert p1["edited_units"] == 0

    assert p2["approved_units"] == 0
    assert p2["flagged_units"] == 1
    assert p2["edited_units"] == 1


def test_export_verified_document_pydantic_valid(db_session, store):
    """Tiêu chuẩn: File JSON xuất ra nạp lại được bằng model Pydantic của schema chung."""
    doc_id = "ND255_2026"
    loaded = store.get(doc_id)

    # Thẩm định viên sửa chữ Điều 1
    unit_id = "ND255_2026_D1"
    new_text = "Nội dung đã thẩm định và sửa chính tả cho Điều 1"
    edit_unit_text(db_session, doc_id, unit_id, user_id=1, new_content=new_text, original_content=loaded.units_by_id[unit_id].content)
    approve_unit(db_session, doc_id, unit_id, user_id=1)

    # Thẩm định viên gắn cờ Điều 2
    flag_unit(db_session, doc_id, "ND255_2026_D2", user_id=1, note="Nghi ngờ số liệu")

    # Xuất tài liệu
    exported = export_verified_document(db_session, loaded)

    # 1. Phải là một instance LegalDocument hợp lệ
    assert isinstance(exported, LegalDocument)

    # 2. Điều 1 phải lấy nội dung mới nhất đã sửa
    d1 = next(u for u in exported.legal_units if u.unit_id == unit_id)
    assert d1.content == new_text
    assert d1.is_confident is True

    # 3. Điều 2 bị gắn cờ phải có is_confident = False
    d2 = next(u for u in exported.legal_units if u.unit_id == "ND255_2026_D2")
    assert d2.is_confident is False

    # 4. Dump thành json string và nạp lại bằng LegalDocument.model_validate_json()
    exported_json = exported.model_dump_json()
    re_parsed = LegalDocument.model_validate_json(exported_json)
    assert re_parsed.document_id == doc_id
    assert len(re_parsed.legal_units) == len(loaded.doc.legal_units)

    # 5. Dữ liệu gốc trong store và file json gốc KHÔNG bị thay đổi
    original_unit = loaded.units_by_id[unit_id]
    assert original_unit.content != new_text


def test_export_parser_feedback(db_session, store):
    """Kiểm tra báo cáo gửi nhóm parser (gồm cờ và lỗi cấu trúc)."""
    doc_id = "ND254_2026"
    loaded = store.get(doc_id)

    flag_unit(db_session, doc_id, "ND254_2026_D5", user_id=1, note="Cần kiểm tra chữ mờ")
    report_structural_error(
        db_session,
        doc_id,
        "ND254_2026_D6",
        user_id=2,
        error_type=StructuralErrorType.WRONG_LEVEL,
        note="Điểm a thực chất là Khoản 1",
    )

    feedback = export_parser_feedback(db_session, loaded)
    assert feedback["document_id"] == doc_id
    assert feedback["flagged_units_count"] == 1
    assert feedback["structural_errors_count"] == 1
    assert feedback["flagged_units"][0]["unit_id"] == "ND254_2026_D5"
    assert feedback["structural_errors"][0]["error_type"] == "wrong_level"


def test_state_persistence_on_reload(db_session, store):
    """Tiêu chuẩn: Tải lại trang thì trạng thái các đơn vị vẫn đúng như đã lưu."""
    doc_id = "ND255_2026"
    loaded = store.get(doc_id)

    unit_id = "ND255_2026_D3"
    approve_unit(db_session, doc_id, unit_id, user_id=1)

    # Mô phỏng reload: truy vấn lại toàn bộ trạng thái đơn vị như khi F5 trang
    reloaded_statuses = get_all_unit_statuses(db_session, doc_id, loaded.units_by_id)
    status = reloaded_statuses[unit_id]

    assert status.decision == DecisionType.APPROVED
    assert status.reviewer_name == "Nguyễn Đức Duy"
