"""FastAPI + Jinja2 + HTMX: màn hình danh sách và màn hình soát hai khung.

Chạy:  uvicorn app.main:app --reload
Thư mục dữ liệu mặc định là ./data, đổi bằng biến môi trường DATA_DIR.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.services.store import DocumentStore, ImportProblem, LoadedDocument
from app.services.views import DEFAULT_ZOOM, build_rows, first_page_of, nearest_zoom, viewer_context

BASE_DIR = Path(__file__).resolve().parent


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    data_path = Path(data_dir or os.environ.get("DATA_DIR") or BASE_DIR.parent / "data")
    store = DocumentStore(data_path)
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

    app = FastAPI(title="Web app thẩm định dữ liệu pháp lý")
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    def load(document_id: str) -> LoadedDocument:
        """Lỗi 404 nếu không có văn bản. ImportProblem được xử lý chung bên dưới."""
        try:
            return store.get(document_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Không có văn bản này")

    @app.exception_handler(ImportProblem)
    async def import_problem_handler(request: Request, exc: ImportProblem):
        return templates.TemplateResponse(request, "import_error.html", {"problem": exc}, status_code=422)

    @app.get("/")
    def document_list(request: Request):
        items = []
        for document_id in store.list_ids():
            try:
                loaded = store.get(document_id)
                items.append({"document_id": document_id, "loaded": loaded, "problem": None})
            except ImportProblem as problem:
                items.append({"document_id": document_id, "loaded": None, "problem": problem})
        return templates.TemplateResponse(request, "list.html", {"items": items})

    @app.get("/documents/{document_id}")
    def review(request: Request, document_id: str, unit: str | None = None, page: int | None = None, zoom: int = DEFAULT_ZOOM):
        loaded = load(document_id)
        units = loaded.units_by_id
        if unit is None and loaded.doc.legal_units:
            unit = loaded.doc.legal_units[0].unit_id
        selected = units.get(unit) if unit else None
        if unit and selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        zoom = nearest_zoom(zoom)
        context = {
            "doc": loaded.doc,
            "unit": selected,
            "rows": build_rows(loaded, unit, zoom),
            "v": viewer_context(loaded, page or first_page_of(selected), zoom, unit),
            "oob": False,
        }
        return templates.TemplateResponse(request, "review.html", context)

    @app.get("/documents/{document_id}/pages/{page}")
    def page_image(document_id: str, page: int):
        loaded = load(document_id)
        if page < 1 or page > loaded.page_count:
            raise HTTPException(status_code=404, detail="Không có trang này")
        try:
            image = loaded.page_image(page)
        except Exception as exc:  # PDF hỏng, thiếu quyền ghi vào thư mục pages...
            raise HTTPException(status_code=500, detail=f"Không render được trang {page}: {exc}")
        return FileResponse(image, media_type="image/png")

    @app.get("/documents/{document_id}/viewer")
    def viewer(request: Request, document_id: str, page: int = 1, zoom: int = DEFAULT_ZOOM, unit: str | None = None):
        """Khung trái (HTMX): đổi trang hoặc đổi mức phóng to."""
        loaded = load(document_id)
        context = {"v": viewer_context(loaded, page, zoom, unit), "oob": False}
        return templates.TemplateResponse(request, "partials/viewer.html", context)

    @app.get("/documents/{document_id}/units/{unit_id}")
    def unit_detail(request: Request, document_id: str, unit_id: str, zoom: int = DEFAULT_ZOOM):
        """Bấm một đơn vị (HTMX): trả chi tiết, kèm khung trái và cây đã cập nhật (out-of-band)."""
        loaded = load(document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        zoom = nearest_zoom(zoom)
        context = {
            "doc": loaded.doc,
            "unit": selected,
            "rows": build_rows(loaded, unit_id, zoom),
            "v": viewer_context(loaded, first_page_of(selected), zoom, unit_id),
            "oob": True,
        }
        return templates.TemplateResponse(request, "unit_response.html", context)

    return app


app = create_app()
