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


def full_label(unit: LegalUnit) -> str:
    """Tên đầy đủ để hiện ở tiêu đề: "Điểm a, Khoản 1, Điều 2"; phụ lục, biểu mẫu thì lấy tên hoặc mã."""
    parts = [f"Điểm {unit.point}" if unit.point is not None else "",
             f"Khoản {unit.clause}" if unit.clause is not None else "",
             f"Điều {unit.article}" if unit.article is not None else ""]
    return ", ".join(part for part in parts if part) or unit.title or unit.unit_id


def tree_order(units: list[LegalUnit]) -> list[str]:
    """Mã đơn vị theo đúng thứ tự hiện trên cây (duyệt sâu, cha trước con) để đi tới đơn vị trước / kế tiếp."""
    known = {unit.unit_id for unit in units}
    children: dict[str | None, list[str]] = {}
    for unit in units:
        parent = unit.parent_unit_id if unit.parent_unit_id in known else None
        children.setdefault(parent, []).append(unit.unit_id)
    order: list[str] = []
    stack = list(reversed(children.get(None, [])))
    while stack:
        unit_id = stack.pop()
        order.append(unit_id)
        stack.extend(reversed(children.get(unit_id, [])))
    return order


def neighbours(order: list[str], unit_id: str) -> tuple[str | None, str | None]:
    """(đơn vị trước, đơn vị kế) của `unit_id` theo thứ tự cây; đầu hoặc cuối cây thì là None."""
    if unit_id not in order:
        return None, None
    i = order.index(unit_id)
    return (order[i - 1] if i > 0 else None), (order[i + 1] if i + 1 < len(order) else None)


def next_unreviewed(order: list[str], unit_id: str, reviews: dict) -> str | None:
    """Đơn vị chưa soát đầu tiên sau `unit_id` theo thứ tự cây, hết cây thì quay lại từ đầu; không còn thì None."""
    start = order.index(unit_id) + 1 if unit_id in order else 0
    for candidate in order[start:] + order[:start]:
        if candidate != unit_id and (candidate not in reviews or reviews[candidate].status == "unreviewed"):
            return candidate
    return None


def unit_snippet(unit: LegalUnit) -> str:
    """Điều: tên Điều (nội dung Điều chỉ lặp lại các Khoản con). Khoản/Điểm/phụ lục: đầu nội dung."""
    if unit.article is not None and unit.clause is None and unit.title:
        return unit.title
    text = " ".join(unit.content.split())
    return text[:SNIPPET_LENGTH]


def build_tree(units: list[LegalUnit], reviews: dict | None = None, marks: dict | None = None) -> list[dict]:
    """Cây lồng nhau theo parent_unit_id, giữ đúng thứ tự trong `units`. Đơn vị không có cha là gốc.

    `reviews`: unit_id -> UnitReview; đơn vị không có trong đó là "unreviewed".
    `marks`: unit_id -> [{"name", "color"}], người chỉnh sửa đã thao tác trên đơn vị (vạch màu, chỉ người phê duyệt /
    Admin thấy)."""
    reviews = reviews or {}
    marks = marks or {}
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
            "marks": marks.get(unit.unit_id, []),
            "children": [],
        }
        nodes[unit.unit_id] = node
        parent = nodes.get(unit.parent_unit_id) if unit.parent_unit_id else None
        (parent["children"] if parent is not None else roots).append(node)
    return roots


def decorate_for_page(tree: list[dict], document_id: str, selected_id: str | None) -> None:
    """Thêm url, selected, open cho từng nút (tại chỗ). `open` = nút là tổ tiên của đơn vị đang chọn."""

    def visit(node: dict) -> bool:
        contains_selected = node["unit_id"] == selected_id
        for child in node["children"]:
            if visit(child):
                contains_selected = True
        node["url"] = f"/documents/{document_id}/units/{quote(node['unit_id'], safe='')}"
        node["selected"] = node["unit_id"] == selected_id
        node["open"] = contains_selected and not node["selected"]
        return contains_selected

    for root in tree:
        visit(root)


def person_marks(loaded) -> dict[str, list[dict]]:
    """unit_id -> người chỉnh sửa đã thao tác trên đơn vị (theo màu của họ trong văn bản)."""
    marks: dict[str, list[dict]] = {}
    if loaded.tracks is None:
        return marks
    for person in loaded.people:
        for unit_id in loaded.tracks.editors.get(person, {}):
            marks.setdefault(unit_id, []).append({"name": person, "color": loaded.color_of(person)})
    return marks
