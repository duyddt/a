"""Nghiệp vụ thẩm định: duyệt, sửa chữ (append-only), gắn cờ, báo lỗi cấu trúc, tiến độ và xuất dữ liệu."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.contracts import LegalDocument, LegalUnit
from app.db.models import DecisionType, ReviewDecision, StructuralErrorType, UnitRevision, User


@dataclass
class UnitStatus:
    unit_id: str
    decision: Optional[DecisionType]
    structural_error_type: Optional[StructuralErrorType]
    note: Optional[str]
    reviewer_name: Optional[str]
    decision_time: Optional[datetime]
    current_content: str
    is_edited: bool
    revision_count: int
    original_content: str


def get_unit_status(db: Session, document_id: str, unit_id: str, original_content: str) -> UnitStatus:
    """Lấy trạng thái quyết định mới nhất và nội dung hiện tại của một đơn vị."""
    # Lấy quyết định mới nhất
    latest_decision = (
        db.query(ReviewDecision)
        .filter(ReviewDecision.document_id == document_id, ReviewDecision.unit_id == unit_id)
        .order_by(desc(ReviewDecision.created_at), desc(ReviewDecision.id))
        .first()
    )

    # Lấy bản sửa mới nhất
    revisions = (
        db.query(UnitRevision)
        .filter(UnitRevision.document_id == document_id, UnitRevision.unit_id == unit_id)
        .order_by(desc(UnitRevision.created_at), desc(UnitRevision.id))
        .all()
    )

    latest_rev = revisions[0] if revisions else None
    current_content = latest_rev.content_after if latest_rev else original_content

    return UnitStatus(
        unit_id=unit_id,
        decision=latest_decision.decision if latest_decision else None,
        structural_error_type=latest_decision.structural_error_type if latest_decision else None,
        note=latest_decision.note if latest_decision else None,
        reviewer_name=latest_decision.user.full_name if latest_decision and latest_decision.user else None,
        decision_time=latest_decision.created_at if latest_decision else None,
        current_content=current_content,
        is_edited=len(revisions) > 0,
        revision_count=len(revisions),
        original_content=original_content,
    )


def get_all_unit_statuses(db: Session, document_id: str, units_by_id: dict[str, LegalUnit]) -> dict[str, UnitStatus]:
    """Lấy trạng thái của toàn bộ đơn vị trong một văn bản (tối ưu truy vấn)."""
    # Lấy tất cả decisions của văn bản
    decisions = (
        db.query(ReviewDecision)
        .filter(ReviewDecision.document_id == document_id)
        .order_by(ReviewDecision.created_at.asc(), ReviewDecision.id.asc())
        .all()
    )
    latest_decision_map: dict[str, ReviewDecision] = {}
    for d in decisions:
        latest_decision_map[d.unit_id] = d

    # Lấy tất cả revisions của văn bản
    revisions = (
        db.query(UnitRevision)
        .filter(UnitRevision.document_id == document_id)
        .order_by(UnitRevision.created_at.asc(), UnitRevision.id.asc())
        .all()
    )
    revisions_map: dict[str, list[UnitRevision]] = {}
    for r in revisions:
        revisions_map.setdefault(r.unit_id, []).append(r)

    statuses: dict[str, UnitStatus] = {}
    for unit_id, unit in units_by_id.items():
        d = latest_decision_map.get(unit_id)
        rev_list = revisions_map.get(unit_id, [])
        latest_rev = rev_list[-1] if rev_list else None
        current_content = latest_rev.content_after if latest_rev else unit.content

        statuses[unit_id] = UnitStatus(
            unit_id=unit_id,
            decision=d.decision if d else None,
            structural_error_type=d.structural_error_type if d else None,
            note=d.note if d else None,
            reviewer_name=d.user.full_name if d and d.user else None,
            decision_time=d.created_at if d else None,
            current_content=current_content,
            is_edited=len(rev_list) > 0,
            revision_count=len(rev_list),
            original_content=unit.content,
        )
    return statuses


def get_document_progress(db: Session, document_id: str, total_units: int) -> dict[str, Any]:
    """Tính toán tiến độ thẩm định của văn bản."""
    # Lấy quyết định mới nhất của từng đơn vị
    subquery = (
        db.query(
            ReviewDecision.unit_id,
            ReviewDecision.decision,
            func.row_number()
            .over(
                partition_by=ReviewDecision.unit_id,
                order_by=(desc(ReviewDecision.created_at), desc(ReviewDecision.id)),
            )
            .label("rn"),
        )
        .filter(ReviewDecision.document_id == document_id)
        .subquery()
    )

    latest_decisions = (
        db.query(subquery.c.decision, func.count(subquery.c.unit_id))
        .filter(subquery.c.rn == 1)
        .group_by(subquery.c.decision)
        .all()
    )

    decision_counts = {d: count for d, count in latest_decisions}
    approved = decision_counts.get(DecisionType.APPROVED, 0)
    flagged = decision_counts.get(DecisionType.FLAGGED, 0)
    structural = decision_counts.get(DecisionType.STRUCTURAL_ERROR, 0)

    # Đếm số đơn vị đã sửa
    edited_count = (
        db.query(func.count(func.distinct(UnitRevision.unit_id)))
        .filter(UnitRevision.document_id == document_id)
        .scalar()
        or 0
    )

    total = max(total_units, 1)
    reviewed = approved + flagged + structural
    remaining = max(0, total_units - reviewed)
    percent = round((approved / total) * 100, 1)

    return {
        "document_id": document_id,
        "total_units": total_units,
        "approved_units": approved,
        "edited_units": edited_count,
        "flagged_units": flagged,
        "structural_error_units": structural,
        "reviewed_units": reviewed,
        "remaining_units": remaining,
        "percent_completed": percent,
    }


def approve_unit(db: Session, document_id: str, unit_id: str, user_id: int) -> ReviewDecision:
    """Ghi nhận quyết định Duyệt cho đơn vị."""
    decision = ReviewDecision(
        document_id=document_id,
        unit_id=unit_id,
        user_id=user_id,
        decision=DecisionType.APPROVED,
        structural_error_type=None,
        note=None,
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return decision


def edit_unit_text(
    db: Session,
    document_id: str,
    unit_id: str,
    user_id: int,
    new_content: str,
    original_content: str,
) -> UnitRevision:
    """Ghi nhận một bản sửa chữ mới (APPEND-ONLY, tuyệt đối không sửa hay xóa dữ liệu cũ)."""
    # Lấy nội dung hiện tại trước khi sửa
    latest_rev = (
        db.query(UnitRevision)
        .filter(UnitRevision.document_id == document_id, UnitRevision.unit_id == unit_id)
        .order_by(desc(UnitRevision.created_at), desc(UnitRevision.id))
        .first()
    )
    current_content = latest_rev.content_after if latest_rev else original_content

    new_content_clean = new_content.strip()
    if not new_content_clean:
        raise ValueError("Nội dung sau khi sửa không được để trống")

    rev = UnitRevision(
        document_id=document_id,
        unit_id=unit_id,
        user_id=user_id,
        content_before=current_content,
        content_after=new_content_clean,
    )
    db.add(rev)
    db.commit()
    db.refresh(rev)
    return rev


def flag_unit(db: Session, document_id: str, unit_id: str, user_id: int, note: str) -> ReviewDecision:
    """Gắn cờ không chắc chắn kèm ghi chú."""
    decision = ReviewDecision(
        document_id=document_id,
        unit_id=unit_id,
        user_id=user_id,
        decision=DecisionType.FLAGGED,
        structural_error_type=None,
        note=note.strip() if note else "Chưa chắc chắn",
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return decision


def report_structural_error(
    db: Session,
    document_id: str,
    unit_id: str,
    user_id: int,
    error_type: StructuralErrorType,
    note: str,
) -> ReviewDecision:
    """Báo lỗi cấu trúc (sai cấp, sai số, cần tách, cần gộp) kèm ghi chú cho nhóm parser."""
    decision = ReviewDecision(
        document_id=document_id,
        unit_id=unit_id,
        user_id=user_id,
        decision=DecisionType.STRUCTURAL_ERROR,
        structural_error_type=error_type,
        note=note.strip() if note else None,
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return decision


def get_unit_history(db: Session, document_id: str, unit_id: str, original_content: str) -> dict[str, Any]:
    """Lấy toàn bộ lịch sử sửa chữ và các lần quyết định thẩm định."""
    revisions = (
        db.query(UnitRevision)
        .filter(UnitRevision.document_id == document_id, UnitRevision.unit_id == unit_id)
        .order_by(UnitRevision.created_at.asc(), UnitRevision.id.asc())
        .all()
    )
    decisions = (
        db.query(ReviewDecision)
        .filter(ReviewDecision.document_id == document_id, ReviewDecision.unit_id == unit_id)
        .order_by(desc(ReviewDecision.created_at), desc(ReviewDecision.id))
        .all()
    )

    current_content = revisions[-1].content_after if revisions else original_content

    return {
        "unit_id": unit_id,
        "original_content": original_content,
        "current_content": current_content,
        "revisions": [
            {
                "id": r.id,
                "content_before": r.content_before,
                "content_after": r.content_after,
                "reviewer": r.user.full_name if r.user else "Không rõ",
                "created_at": r.created_at.isoformat(),
            }
            for r in revisions
        ],
        "decisions": [
            {
                "id": d.id,
                "decision": d.decision.value,
                "structural_error_type": d.structural_error_type.value if d.structural_error_type else None,
                "note": d.note,
                "reviewer": d.user.full_name if d.user else "Không rõ",
                "created_at": d.created_at.isoformat(),
            }
            for d in decisions
        ],
    }


def export_verified_document(db: Session, loaded_document: Any) -> LegalDocument:
    """Xuất văn bản đã thẩm định theo đúng schema chung (LegalDocument).
    Nội dung content lấy bản sửa mới nhất từ unit_revisions nếu có.
    """
    doc_dict = loaded_document.doc.model_dump()
    document_id = doc_dict["document_id"]

    # Lấy bản sửa mới nhất của từng đơn vị
    revisions = (
        db.query(UnitRevision)
        .filter(UnitRevision.document_id == document_id)
        .order_by(UnitRevision.created_at.asc(), UnitRevision.id.asc())
        .all()
    )
    rev_map: dict[str, str] = {}
    for r in revisions:
        rev_map[r.unit_id] = r.content_after

    # Lấy quyết định mới nhất
    decisions = (
        db.query(ReviewDecision)
        .filter(ReviewDecision.document_id == document_id)
        .order_by(ReviewDecision.created_at.asc(), ReviewDecision.id.asc())
        .all()
    )
    decision_map: dict[str, ReviewDecision] = {}
    for d in decisions:
        decision_map[d.unit_id] = d

    for unit in doc_dict["legal_units"]:
        u_id = unit["unit_id"]
        # Cập nhật nội dung sửa mới nhất nếu có
        if u_id in rev_map:
            unit["content"] = rev_map[u_id]

        # Đánh dấu is_confident
        dec = decision_map.get(u_id)
        if dec:
            if dec.decision == DecisionType.APPROVED:
                unit["is_confident"] = True
            elif dec.decision in (DecisionType.FLAGGED, DecisionType.STRUCTURAL_ERROR):
                unit["is_confident"] = False

    # Xác thực lại bằng đúng model Pydantic của schema chung
    return LegalDocument.model_validate(doc_dict)


def export_parser_feedback(db: Session, loaded_document: Any) -> dict[str, Any]:
    """Xuất danh sách các đơn vị bị gắn cờ và lỗi cấu trúc kèm ghi chú cho nhóm parser."""
    document_id = loaded_document.doc.document_id
    units_by_id = loaded_document.units_by_id

    decisions = (
        db.query(ReviewDecision)
        .filter(ReviewDecision.document_id == document_id)
        .order_by(ReviewDecision.created_at.asc(), ReviewDecision.id.asc())
        .all()
    )
    latest_decision_map: dict[str, ReviewDecision] = {}
    for d in decisions:
        latest_decision_map[d.unit_id] = d

    flagged_units = []
    structural_errors = []

    for unit_id, dec in latest_decision_map.items():
        unit = units_by_id.get(unit_id)
        source_pages = unit.source_pages if unit else []
        title = unit.title if unit else ""

        if dec.decision == DecisionType.FLAGGED:
            flagged_units.append(
                {
                    "unit_id": unit_id,
                    "title": title,
                    "source_pages": source_pages,
                    "note": dec.note,
                    "reviewer": dec.user.full_name if dec.user else "Không rõ",
                    "created_at": dec.created_at.isoformat(),
                }
            )
        elif dec.decision == DecisionType.STRUCTURAL_ERROR:
            structural_errors.append(
                {
                    "unit_id": unit_id,
                    "title": title,
                    "source_pages": source_pages,
                    "error_type": dec.structural_error_type.value if dec.structural_error_type else None,
                    "note": dec.note,
                    "reviewer": dec.user.full_name if dec.user else "Không rõ",
                    "created_at": dec.created_at.isoformat(),
                }
            )

    return {
        "document_id": document_id,
        "document_no": loaded_document.doc.document_no,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "flagged_units_count": len(flagged_units),
        "structural_errors_count": len(structural_errors),
        "flagged_units": flagged_units,
        "structural_errors": structural_errors,
    }
