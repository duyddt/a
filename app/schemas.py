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


class DocumentSummaryOut(BaseModel):
    document_id: str = Field(examples=["ND254_2026"])
    document_no: str | None = Field(default=None, examples=["254/2026/NĐ-CP"])
    document_type: str | None = None
    title: str | None = None
    page_count: int | None = Field(default=None, description="Số trang PDF gốc")
    unit_count: int | None = Field(default=None, description="Tổng số đơn vị (Điều + Khoản + Điểm + phụ lục)")
    progress: ProgressOut | None = Field(default=None, description="Tiến độ thẩm định; null khi chạy chế độ thư mục file")


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
    children: list["UnitNodeOut"] = Field(default_factory=list)


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


ReviewAction = Literal["approve", "edit", "flag", "structure_error", "reset"]


class ReviewRequest(BaseModel):
    action: ReviewAction = Field(description="approve = Duyệt; edit = Sửa chữ; flag = Gắn cờ; structure_error = Báo lỗi cấu trúc; reset = Bỏ đánh dấu")
    reviewer: str = Field(description="Tên người soát", examples=["Duy"])
    note: str | None = Field(default=None, description="Lý do. Bắt buộc với flag và structure_error")
    content: str | None = Field(default=None, description="Nội dung mới. Bắt buộc với edit")


class ReviewEventOut(BaseModel):
    id: int
    unit_id: str
    action: ReviewAction
    reviewer: str
    note: str | None = None
    content_before: str | None = None
    content_after: str | None = None
    created_at: datetime | None = None


class ReviewResultOut(BaseModel):
    event: ReviewEventOut
    review_status: Literal["unreviewed", "approved", "flagged", "structure_error"] = Field(description="Trạng thái của đơn vị sau thao tác")
    progress: ProgressOut
