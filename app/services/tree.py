"""Dựng cây Điều → Khoản → Điểm từ danh sách đơn vị. Thuần Python, không phụ thuộc FastAPI hay DB."""
from __future__ import annotations

from urllib.parse import quote

from app.contracts import LegalUnit

from .review import STATUS_LABELS as REVIEW_STATUS_LABELS  # noqa: F401  (giữ tên cũ cho nơi khác dùng)
SNIPPET_LENGTH = 80


def unit_label(unit: LegalUnit) -> str:
    if unit.point is not None:
        return f"Điểm {unit.point}"
    if unit.clause is not None:
        return f"Khoản {unit.clause}"
    if unit.article is not None:
        return f"Điều {unit.article}"
    return unit.title or unit.unit_id  # phụ lục, biểu mẫu


def unit_snippet(unit: LegalUnit) -> str:
    """Điều: tên Điều (nội dung Điều chỉ lặp lại các Khoản con). Khoản/Điểm/phụ lục: đầu nội dung."""
    if unit.article is not None and unit.clause is None and unit.title:
        return unit.title
    text = " ".join(unit.content.split())
    return text[:SNIPPET_LENGTH]


def build_tree(units: list[LegalUnit], reviews: dict | None = None) -> list[dict]:
    """Cây lồng nhau theo parent_unit_id, giữ đúng thứ tự trong `units`. Đơn vị không có cha là gốc.

    `reviews`: unit_id -> UnitReview; đơn vị không có trong đó là "unreviewed"."""
    reviews = reviews or {}
    nodes: dict[str, dict] = {}
    roots: list[dict] = []
    for unit in units:
        status = reviews[unit.unit_id].status if unit.unit_id in reviews else "unreviewed"
        node = {
            "unit_id": unit.unit_id,
            "label": unit_label(unit),
            "article": unit.article,
            "clause": unit.clause,
            "point": unit.point,
            "title": unit.title,
            "snippet": unit_snippet(unit),
            "is_confident": unit.is_confident,
            "status": unit.status,
            "review_status": status,
            "review_status_label": REVIEW_STATUS_LABELS[status],
            "source_pages": list(unit.source_pages),
            "children": [],
        }
        nodes[unit.unit_id] = node
        parent = nodes.get(unit.parent_unit_id) if unit.parent_unit_id else None
        (parent["children"] if parent is not None else roots).append(node)
    return roots


def decorate_for_page(tree: list[dict], document_id: str, selected_id: str | None, zoom: int) -> None:
    """Thêm url, selected, open cho từng nút (tại chỗ). `open` = nút là tổ tiên của đơn vị đang chọn."""

    def visit(node: dict) -> bool:
        contains_selected = node["unit_id"] == selected_id
        for child in node["children"]:
            if visit(child):
                contains_selected = True
        node["url"] = f"/documents/{document_id}/units/{quote(node['unit_id'], safe='')}?zoom={zoom}"
        node["selected"] = node["unit_id"] == selected_id
        node["open"] = contains_selected and not node["selected"]
        return contains_selected

    for root in tree:
        visit(root)
