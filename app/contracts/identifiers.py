"""Định danh theo docs/contracts.md (bản tối giản, TẠM THỜI).

Thay bằng bản sao nguyên văn backend/app/contracts/identifiers.py của repo chính khi có.
"""
from __future__ import annotations

import re

_TYPE_PREFIX = {
    "Hiến pháp": "HP",
    "Bộ luật": "BL",
    "Luật": "L",
    "Nghị quyết": "NQ",
    "Nghị định": "ND",
    "Quyết định": "QD",
    "Thông tư": "TT",
    "Văn bản hợp nhất": "VBHN",
}

_DOC_NO = re.compile(r"^(\d+)/(\d{4})/")


def make_document_id(document_type: str, document_no: str) -> str:
    """("Nghị định", "254/2026/NĐ-CP") -> "ND254_2026". Báo lỗi nếu số hiệu không có dạng <số>/<năm>/..."""
    prefix = _TYPE_PREFIX.get(document_type)
    match = _DOC_NO.match(document_no)
    if prefix is None or match is None:
        raise ValueError(f"không tạo được document_id từ ({document_type!r}, {document_no!r})")
    return f"{prefix}{match.group(1)}_{match.group(2)}"


def make_unit_id(document_id: str, article: int, clause: int | None = None, point: str | None = None) -> str:
    """("ND254_2026", 9, 1, "a") -> "ND254_2026_D9_K1_a"."""
    if point is not None and clause is None:
        raise ValueError("Điểm phải thuộc một Khoản")
    unit_id = f"{document_id}_D{article}"
    if clause is not None:
        unit_id += f"_K{clause}"
    if point is not None:
        unit_id += f"_{point}"
    return unit_id
