"""Chuẩn bị dữ liệu cho template: cây Điều/Khoản/Điểm và khung xem trang. Không phụ thuộc FastAPI."""
from __future__ import annotations

from urllib.parse import quote

from app.contracts import LegalUnit

from .store import LoadedDocument

ZOOMS = [50, 75, 100, 125, 150, 200, 300]  # % chiều rộng khung; 100 = vừa chiều rộng
DEFAULT_ZOOM = 100


def nearest_zoom(zoom: int) -> int:
    return min(ZOOMS, key=lambda z: abs(z - zoom))


def unit_label(unit: LegalUnit) -> str:
    if unit.point is not None:
        return f"Điểm {unit.point}"
    if unit.clause is not None:
        return f"Khoản {unit.clause}"
    if unit.article is not None:
        return f"Điều {unit.article}"
    return unit.title or unit.unit_id  # phụ lục, biểu mẫu


def unit_url(document_id: str, unit_id: str, zoom: int) -> str:
    return f"/documents/{document_id}/units/{quote(unit_id, safe='')}?zoom={zoom}"


def build_rows(loaded: LoadedDocument, selected_id: str | None, zoom: int) -> list[dict]:
    """Một dòng cho mỗi đơn vị, theo thứ tự trong file (cha đứng trước con)."""
    rows = []
    for unit in loaded.doc.legal_units:
        depth = 2 if unit.point is not None else 1 if unit.clause is not None else 0
        if unit.clause is None and unit.point is None:
            snippet = unit.title or " ".join(unit.content.split())
        else:
            snippet = " ".join(unit.content.split())
        rows.append(
            {
                "unit_id": unit.unit_id,
                "depth": depth,
                "label": unit_label(unit),
                "snippet": snippet[:80],
                "confident": unit.is_confident,
                "selected": unit.unit_id == selected_id,
                "url": unit_url(loaded.doc.document_id, unit.unit_id, zoom),
            }
        )
    return rows


def viewer_context(loaded: LoadedDocument, page: int, zoom: int, unit_id: str | None) -> dict:
    """Mọi URL của khung trái được dựng ở đây để template không phải ghép chuỗi."""
    document_id = loaded.doc.document_id
    page = min(max(page, 1), loaded.page_count)
    zoom = nearest_zoom(zoom)
    base = f"/documents/{document_id}/viewer"

    def url(target_page: int, target_zoom: int) -> str:
        query = f"?page={target_page}&zoom={target_zoom}"
        if unit_id:
            query += f"&unit={quote(unit_id, safe='')}"
        return base + query

    unit = loaded.units_by_id.get(unit_id) if unit_id else None
    chips = [{"page": p, "url": url(p, zoom), "current": p == page} for p in (unit.source_pages if unit else [])]
    zoom_index = ZOOMS.index(zoom)
    return {
        "base": base,
        "document_id": document_id,
        "unit_id": unit_id,
        "page": page,
        "page_count": loaded.page_count,
        "zoom": zoom,
        "image_url": f"/documents/{document_id}/pages/{page}",
        "prev_url": url(page - 1, zoom) if page > 1 else None,
        "next_url": url(page + 1, zoom) if page < loaded.page_count else None,
        "zoom_out_url": url(page, ZOOMS[zoom_index - 1]) if zoom_index > 0 else None,
        "zoom_in_url": url(page, ZOOMS[zoom_index + 1]) if zoom_index < len(ZOOMS) - 1 else None,
        "fit_url": url(page, DEFAULT_ZOOM),
        "chips": chips,
    }


def first_page_of(unit: LegalUnit | None) -> int:
    return unit.source_pages[0] if unit and unit.source_pages else 1
