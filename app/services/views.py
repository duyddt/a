"""Chuẩn bị dữ liệu khung xem trang cho template. Không phụ thuộc FastAPI."""
from __future__ import annotations

from datetime import timedelta, timezone
from urllib.parse import quote

from app.contracts import LegalUnit

from .models import LoadedDocument

VN_TIME = timezone(timedelta(hours=7))  # cố định UTC+7, không cần cơ sở dữ liệu múi giờ của hệ điều hành
ZOOMS = [50, 75, 100, 125, 150, 200, 300]  # % chiều rộng khung; 100 = vừa chiều rộng
DEFAULT_ZOOM = 100


def nearest_zoom(zoom: int) -> int:
    return min(ZOOMS, key=lambda z: abs(z - zoom))


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


def vn_time(value) -> str:
    """Giờ Việt Nam, ví dụ 31/10/2026 07:45."""
    return value.astimezone(VN_TIME).strftime("%d/%m/%Y %H:%M") if value else ""
