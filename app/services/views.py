"""Chuẩn bị dữ liệu khung xem trang cho template. Không phụ thuộc FastAPI."""
from __future__ import annotations

from datetime import timedelta, timezone
from urllib.parse import quote

from app.contracts import LegalUnit

from .highlight import NO_HIGHLIGHT, locate
from .models import LoadedDocument
from .render_pdf import page_aspect

VN_TIME = timezone(timedelta(hours=7))  # cố định UTC+7, không cần cơ sở dữ liệu múi giờ của hệ điều hành
FIT = 0  # mức thu phóng đặc biệt: cả trang nằm gọn trong khung, giữ đúng tỉ lệ trang
ZOOMS = [50, 75, 100, 125, 150, 200, 300]  # % chiều rộng khung; 100 = vừa chiều rộng
DEFAULT_ZOOM = FIT


def nearest_zoom(zoom: int) -> int:
    if zoom <= FIT:
        return FIT
    return min(ZOOMS, key=lambda z: abs(z - zoom))


def _percent_box(rect) -> dict:
    """Toạ độ % theo ảnh trang, để khung tô co giãn theo mức phóng to."""
    left, top, right, bottom = rect
    return {"left": round(left * 100, 2), "top": round(top * 100, 2),
            "width": round((right - left) * 100, 2), "height": round((bottom - top) * 100, 2)}


def viewer_context(loaded: LoadedDocument, page: int, zoom: int, unit_id: str | None) -> dict:
    """Mọi URL và số liệu của khung trái được dựng ở đây để template không phải ghép chuỗi.
    Mỗi lần chỉ hiện một trang; đơn vị nằm trên nhiều trang thì chuyển trang bằng các nút số trang."""
    document_id = loaded.doc.document_id
    page = min(max(page, 1), loaded.page_count)
    zoom = nearest_zoom(zoom)
    base = f"/documents/{document_id}/viewer"

    def url(target_page: int, target_zoom: int = zoom) -> str:
        query = f"?page={target_page}&zoom={target_zoom}"
        if unit_id:
            query += f"&unit={quote(unit_id, safe='')}"
        return base + query

    unit = loaded.units_by_id.get(unit_id) if unit_id else None
    mark = locate(loaded, unit.unit_id, page) if unit else NO_HIGHLIGHT
    chips = [{"page": p, "url": url(p), "current": p == page} for p in (unit.source_pages if unit else [])]
    return {
        "base": base,
        "document_id": document_id,
        "unit_id": unit_id,
        "page": page,
        "page_count": loaded.page_count,
        "image_url": f"/documents/{document_id}/pages/{page}",
        "aspect": round(page_aspect(loaded.pdf_path, page), 4),  # cao / rộng, để giữ đúng tỉ lệ trang
        "zoom": zoom,
        "zooms": ZOOMS,
        "fit": zoom == FIT,
        "frame_width": 100 if zoom == FIT else zoom,
        "prev_url": url(page - 1) if page > 1 else None,
        "next_url": url(page + 1) if page < loaded.page_count else None,
        "fit_width_url": url(page, 100),
        "fit_url": url(page, FIT),
        "chips": chips,
        "highlights": [_percent_box(rect) for rect in mark.rects],
        "highlight_approximate": mark.method == "layout",  # bản scan không có lớp chữ: vị trí ước lượng
    }


def first_page_of(unit: LegalUnit | None) -> int:
    return unit.source_pages[0] if unit and unit.source_pages else 1


def vn_time(value) -> str:
    """Giờ Việt Nam, ví dụ 31/10/2026 07:45."""
    return value.astimezone(VN_TIME).strftime("%d/%m/%Y %H:%M") if value else ""


def unit_versions(loaded, unit_id: str, everyone: bool) -> list[dict]:
    """Các bản của một đơn vị để so sánh. `everyone` (người phê duyệt / Admin): bản OCR gốc, bản của từng người chỉnh
    sửa đã thao tác trên đơn vị (kèm số màu của người đó), rồi bản chốt. Người chỉnh sửa: bản OCR gốc và bản của mình."""
    from .review import STATUS_LABELS

    tracks = loaded.tracks
    final_text = loaded.text_of(unit_id, "final")

    def entry(version: str, label: str, state=None, color: int | None = None) -> dict:
        status = None if version == "ocr" else (state.status if state is not None else "unreviewed")
        content = loaded.text_of(unit_id, version)
        return {
            "version": version, "label": label, "content": content, "status": status,
            "status_label": STATUS_LABELS[status] if status else None,
            "note": state.note if state is not None else None,
            "edit_count": state.edit_count if state is not None else 0, "color": color,
            "same_as_final": content == final_text,
        }

    versions = [entry("ocr", "Bản OCR gốc")]
    if tracks is None:
        return versions
    if everyone:
        for person in loaded.people:
            state = tracks.editors.get(person, {}).get(unit_id)
            if state is not None:
                versions.append(entry(person, person, state, loaded.color_of(person)))
        versions.append(entry("final", "Bản chốt", tracks.final.get(unit_id)))
    elif loaded.version != "final":
        person = loaded.version
        versions.append(entry(person, "Bản của bạn", tracks.editors.get(person, {}).get(unit_id), loaded.color_of(person)))
    return versions
