"""Định dạng response của API (hiện ở trang /docs tự sinh của FastAPI)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ProgressOut(BaseModel):
    total: int = Field(description="Tổng số đơn vị")
    approved: int
    flagged: int
    structure_error: int
    unreviewed: int
    percent_approved: int = Field(description="Phần trăm đã duyệt, làm tròn")


class CategoryRefOut(BaseModel):
    code: str = Field(examples=["II"], description="Mã danh mục (số La Mã của thư mục trong bộ dữ liệu)")
    name: str = Field(examples=["Hóa đơn điện tử"])


class CategoryOut(CategoryRefOut):
    position: int = Field(description="Thứ tự hiển thị (I = 1 … X = 10)")
    document_count: int
    documents: list[str] = Field(description="Mã các văn bản trong danh mục, theo thứ tự của tệp danh mục")


class DocumentSummaryOut(BaseModel):
    document_id: str = Field(examples=["ND254_2026"])
    document_no: str | None = Field(default=None, examples=["254/2026/NĐ-CP"])
    document_type: str | None = None
    title: str | None = None
    page_count: int | None = Field(default=None, description="Số trang PDF gốc")
    unit_count: int | None = Field(default=None, description="Tổng số đơn vị (Điều + Khoản + Điểm + phụ lục)")
    progress: ProgressOut | None = Field(default=None, description="Tiến độ thẩm định; null khi chạy chế độ thư mục file")
    categories: list[CategoryRefOut] = Field(default_factory=list, description="Các danh mục chứa văn bản (có thể nhiều)")


class UnitNodeOut(BaseModel):
    """Một nút của cây. Nội dung đầy đủ lấy ở GET /api/documents/{id}/units/{unit_id}."""

    unit_id: str = Field(examples=["ND254_2026_D9_K1_a"])
    label: str = Field(description="Ký hiệu hiển thị: Điều 9, Khoản 1, Điểm a", examples=["Điểm a"])
    article: int | None = None
    clause: int | None = None
    point: str | None = None
    title: str | None = Field(default=None, description="Tên Điều (lặp lại ở Khoản/Điểm của Điều đó)")
    snippet: str = Field(description="Tên Điều, hoặc 80 ký tự đầu của nội dung")
    is_confident: bool = Field(description="false = parser không chắc chắn, cần người soát")
    status: str = Field(description="Tình trạng hiệu lực: active, expired, ...")
    review_status: Literal["unreviewed", "approved", "flagged", "structure_error"]
    review_status_label: str
    source_pages: list[int] = Field(description="Số trang PDF (đếm từ 1) chứa đơn vị; khung trái nhảy tới trang đầu tiên")
    children: list[UnitNodeOut] = Field(default_factory=list)


UnitNodeOut.model_rebuild()


class DocumentOut(DocumentSummaryOut):
    issuer: str | None = None
    issued_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    status: str
    amended_by: list[str] = Field(default_factory=list)
    replaced_by: str | None = None
    tree: list[UnitNodeOut] = Field(description="Cây Điều → Khoản → Điểm, đúng thứ tự trong văn bản")


class ImportResultOut(BaseModel):
    document_id: str
    status: Literal["created", "unchanged", "replaced"]
    unit_count: int
    page_count: int


ReviewAction = Literal["approve", "edit", "flag", "structure_error", "reset", "adopt"]


class ReviewRequest(BaseModel):
    action: ReviewAction = Field(description="approve = Duyệt; edit = Sửa chữ; flag = Gắn cờ; structure_error = Báo lỗi cấu trúc; "
                                             "reset = Bỏ đánh dấu; adopt = Chọn bản (người phê duyệt / Admin)")
    reviewer: str | None = Field(default=None, description="Không dùng nữa: người soát là tài khoản đã xác thực (cookie phiên hoặc HTTP Basic)")
    note: str | None = Field(default=None, description="Lý do. Bắt buộc với flag và structure_error")
    content: str | None = Field(default=None, description="Nội dung mới. Bắt buộc với edit")
    source: str | None = Field(default=None, description="Với adopt: bản được chọn, 'ocr' hoặc tên một người chỉnh sửa")


class ReviewEventOut(BaseModel):
    id: int
    unit_id: str
    action: ReviewAction
    reviewer: str
    note: str | None = None
    content_before: str | None = None
    content_after: str | None = None
    created_at: datetime | None = None
    track: Literal["editor", "final"] = Field(default="final", description="editor = bản riêng của `reviewer`; final = bản chốt")


class VersionOut(BaseModel):
    version: str = Field(description="ocr = bản OCR gốc; final = bản chốt; còn lại là tên người chỉnh sửa")
    label: str
    content: str
    status: Literal["unreviewed", "approved", "flagged", "structure_error"] | None = None
    note: str | None = None
    edit_count: int = 0
    color: int | None = Field(default=None, description="Số màu của người chỉnh sửa trong văn bản (1..8)")


class ReviewResultOut(BaseModel):
    event: ReviewEventOut
    review_status: Literal["unreviewed", "approved", "flagged", "structure_error"] = Field(description="Trạng thái của đơn vị sau thao tác")
    progress: ProgressOut


WorkflowStatus = Literal["unassigned", "editing", "returned", "pending", "approved"]


class WorkflowEditorOut(BaseModel):
    username: str
    submitted: bool = Field(description="Đã bấm Hoàn thành")
    submitted_at: datetime | None = None
    color: int = Field(description="Số màu của người chỉnh sửa trong văn bản (1..8), theo thứ tự giao")


class WorkflowOut(BaseModel):
    document_id: str
    status: WorkflowStatus = Field(description="unassigned = Chưa phân công; editing = Đang chỉnh sửa; returned = Trả lại; "
                                               "pending = Chờ phê duyệt; approved = Đã phê duyệt")
    status_label: str
    editors: list[WorkflowEditorOut] = Field(default_factory=list)
    approver: str | None = Field(default=None, description="None: mọi người phê duyệt đều nhận được văn bản")
    returned_note: str | None = None
    returned_by: str | None = None
    finalized_by: str | None = None
    finalized_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowRequest(BaseModel):
    action: Literal["assign", "unassign", "submit", "reopen", "return", "finalize"] = Field(
        description="submit = Hoàn thành (người chỉnh sửa); return = Trả lại, finalize = Hoàn tất phê duyệt (người phê "
                    "duyệt / Admin); assign, unassign, reopen: chỉ Admin")
    note: str | None = Field(default=None, description="Bắt buộc với return")
    target_user: str | None = Field(default=None, description="Với assign / unassign / reopen (một người chỉnh sửa)")
    target_role: Literal["editor", "approver"] | None = Field(default=None, description="Với assign / unassign")


class PersonStatsOut(BaseModel):
    username: str
    display_name: str
    role: str
    assigned: int = Field(description="Số văn bản được giao")
    completed: int = Field(description="Người chỉnh sửa: đã bấm Hoàn thành; người phê duyệt: đã phê duyệt xong")
    percent: int = Field(description="completed / assigned (%)")
    waiting: int = Field(description="Người phê duyệt: văn bản đang chờ mình")
    units_done: int
    units_total: int
    last_activity: datetime | None = None


class DocumentStatsOut(BaseModel):
    document_id: str
    title: str | None = None
    categories: list[str]
    status: WorkflowStatus
    unit_count: int
    final_approved: int = Field(description="Số đơn vị đã duyệt trên bản chốt")
    approver: str | None = None
    editors: list[dict]


class StatsOut(BaseModel):
    total_documents: int
    status_counts: dict[str, int]
    people: list[PersonStatsOut]
    documents: list[DocumentStatsOut]
