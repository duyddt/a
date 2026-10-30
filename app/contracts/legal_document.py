"""LegalDocument / LegalUnit: model tối giản theo docs/contracts.md (TẠM THỜI).

Chỉ có các trường và kiểm tra mà web app cần. Khi có bản sao backend/app/contracts/ của repo chính thì
thay cả thư mục app/contracts/ bằng bản đó; phần còn lại của app chỉ dùng các tên trường dưới đây.

Trường `source_pages` là trường ĐỀ XUẤT thêm vào schema chung (xem README.md, quyết định 4).
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .identifiers import make_unit_id

EffectStatus = Literal["active", "not_yet_effective", "partially_expired", "expired", "replaced"]


class LegalUnit(BaseModel):
    unit_id: str
    parent_unit_id: str | None = None
    document_no: str | None = None  # bỏ trống trong file; LegalDocument tự điền
    chapter: str | None = None  # số La Mã, "II"
    section: str | None = None  # số Mục, "1"
    article: int | None = None
    clause: int | None = None
    point: str | None = None  # a, b, đ...
    title: str | None = None  # tên Điều, lặp lại ở Khoản/Điểm
    content: str  # nguyên văn, gồm cả đơn vị con, không gồm dòng "Điều N. ..."
    effective_from: date | None = None
    effective_to: date | None = None  # nửa mở [from, to)
    status: EffectStatus = "active"
    is_confident: bool = True
    source_pages: list[int] = Field(default_factory=list)  # số trang PDF, đếm từ 1


class LegalDocument(BaseModel):
    document_id: str
    document_no: str
    document_type: str
    title: str
    issuer: str | None = None
    issued_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    status: EffectStatus = "active"
    amended_by: list[str] = Field(default_factory=list)
    replaced_by: str | None = None
    legal_units: list[LegalUnit] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_units(self) -> "LegalDocument":
        errors: list[str] = []
        index: dict[str, int] = {}
        for position, unit in enumerate(self.legal_units):
            if unit.unit_id in index:
                errors.append(f"unit_id trùng trong văn bản: {unit.unit_id}")
            index.setdefault(unit.unit_id, position)

        for position, unit in enumerate(self.legal_units):
            if unit.document_no is None:
                unit.document_no = self.document_no
            elif unit.document_no != self.document_no:
                errors.append(f"{unit.unit_id}: document_no {unit.document_no!r} khác văn bản {self.document_no!r}")

            if unit.article is None:
                continue  # phụ lục, biểu mẫu: tự đặt unit_id, không có cha suy ra được

            if unit.point is not None and unit.clause is None:
                errors.append(f"{unit.unit_id}: có Điểm nhưng không có Khoản")
                continue
            expected = make_unit_id(self.document_id, unit.article, unit.clause, unit.point)
            if unit.unit_id != expected:
                errors.append(f"unit_id {unit.unit_id!r} sai quy ước, mong đợi {expected!r}")

            if unit.point is not None:
                derived_parent = make_unit_id(self.document_id, unit.article, unit.clause)
            elif unit.clause is not None:
                derived_parent = make_unit_id(self.document_id, unit.article)
            else:
                derived_parent = None

            if unit.parent_unit_id is None:
                unit.parent_unit_id = derived_parent
            elif unit.parent_unit_id != derived_parent:
                errors.append(f"{unit.unit_id}: parent_unit_id {unit.parent_unit_id!r} khác cha suy ra {derived_parent!r}")

            if derived_parent in index and index[derived_parent] > position:
                errors.append(f"{unit.unit_id}: đơn vị cha {derived_parent} đứng sau đơn vị con")

        if errors:
            raise ValueError("; ".join(errors))
        return self
