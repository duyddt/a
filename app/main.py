"""FastAPI: API JSON (/api, xem /docs) và giao diện hai khung (Jinja2 + HTMX) dùng chung một nguồn dữ liệu.

Chạy:  uvicorn app.main:app --reload
- Có POSTGRES_HOST hoặc DATABASE_URL: dùng PostgreSQL (migration tự chạy khi khởi động).
- Không có: đọc thư mục data/ (chế độ xem nhanh, không nhập được văn bản qua API).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import get_catalog, load_or_404
from app.api import router as api_router
from app.config import Settings, load_settings
from app.services.models import ImportProblem
from app.services.review import (
    build_inline_diff_html,
    extract_char_diffs,
    ACTION_LABELS,
    OCR_REASONS,
    ReviewProblem,
    build_ocr_report,
    progress,
    resume_unit_id,
)
from app.services.store import DocumentStore
from app.services.tree import build_tree, decorate_for_page
from app.services.views import DEFAULT_ZOOM, first_page_of, nearest_zoom, viewer_context, vn_time

BASE_DIR = Path(__file__).resolve().parent
log = logging.getLogger("review-app")
REVIEWER_COOKIE = "reviewer"


def progress_of(loaded) -> dict | None:
    if not loaded.can_review:
        return None
    p = progress([u.unit_id for u in loaded.doc.legal_units], loaded.reviews)
    report = build_ocr_report(loaded.doc, loaded.reviews)
    p["char_accuracy_percent"] = report["char_accuracy_percent"]
    p["unit_accuracy_percent"] = report["unit_accuracy_percent"]
    p["edited_units"] = report["edited_units"]
    p["ocr_report"] = report
    return p


def create_app(settings: Settings | None = None, catalog=None) -> FastAPI:
    """catalog: truyền sẵn một nguồn dữ liệu (dùng trong test); không thì chọn theo settings."""
    settings = settings or load_settings()
    use_db = catalog is None and settings.conninfo is not None
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    templates.env.filters["vn_time"] = vn_time
    templates.env.globals["ACTION_LABELS"] = ACTION_LABELS
    templates.env.globals["OCR_REASONS"] = OCR_REASONS

    def detail_context(request: Request, loaded, unit, **extra) -> dict:
        """Dữ liệu cho khung chi tiết đơn vị (nội dung, trạng thái, các nút thẩm định, lịch sử)."""
        unit_review = loaded.reviews.get(unit.unit_id) if unit else None
        inline_diff = None
        char_diffs = []
        if unit and unit_review and unit_review.edit_count > 0 and unit_review.original_content is not None:
            inline_diff = build_inline_diff_html(unit_review.original_content, unit.content)
            char_diffs = extract_char_diffs(unit_review.original_content, unit.content)
        context = {
            "doc": loaded.doc,
            "unit": unit,
            "inline_diff": inline_diff,
            "char_diffs": char_diffs,
            "can_review": loaded.can_review,
            "review": loaded.reviews.get(unit.unit_id) if unit else None,
            "history": list(reversed(loaded.events_of(unit.unit_id))) if unit else [],
            "reviewer": unquote(request.cookies.get(REVIEWER_COOKIE, "")),
            "errors": [],
            "form_note": "",
            "form_content": None,
        }
        context.update(extra)
        return context

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = None
        if use_db:
            from app.db import create_pool, migrate
            from app.services.db_catalog import DbCatalog
            from app.services.importer import Importer

            pool = create_pool(settings.conninfo)
            for name in migrate(pool):
                log.info("Đã áp dụng migration %s", name)
            app.state.catalog = DbCatalog(pool, settings.storage_dir)
            app.state.importer = Importer(pool, settings.storage_dir)
            if settings.auto_import_dir:
                for name, outcome in app.state.importer.import_all(settings.auto_import_dir):
                    log.info("Nhập tự động %s: %s", name, outcome)
        try:
            yield
        finally:
            if pool is not None:
                pool.close()

    app = FastAPI(title="Web app thẩm định dữ liệu pháp lý", lifespan=lifespan)
    app.state.catalog = catalog or (None if use_db else DocumentStore(settings.data_dir))
    app.state.importer = None  # chế độ DB gán trong lifespan
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.include_router(api_router)

    @app.exception_handler(ReviewProblem)
    async def review_problem_handler(request: Request, exc: ReviewProblem):
        return JSONResponse(status_code=422, content={"detail": "Thao tác không hợp lệ", "problems": exc.problems})

    @app.exception_handler(ImportProblem)
    async def import_problem_handler(request: Request, exc: ImportProblem):
        if request.url.path.startswith("/api"):
            return JSONResponse(
                status_code=422,
                content={"detail": f"Không nhập được {exc.document_id}", "problems": exc.problems, "truncated": exc.truncated},
            )
        return templates.TemplateResponse(request, "import_error.html", {"problem": exc}, status_code=422)

    @app.get("/", include_in_schema=False)
    def document_list(request: Request, catalog=Depends(get_catalog)):
        return templates.TemplateResponse(request, "list.html", {"items": catalog.list_documents()})

    @app.get("/documents/{document_id}", include_in_schema=False)
    def review(request: Request, document_id: str, unit: str | None = None, page: int | None = None,
               zoom: int = DEFAULT_ZOOM, catalog=Depends(get_catalog)):
        loaded = load_or_404(catalog, document_id)
        units = loaded.units_by_id
        if unit is None and loaded.doc.legal_units:
            unit = resume_unit_id(
                [u.unit_id for u in loaded.doc.legal_units],
                loaded.reviews,
                loaded.events,
            )
        selected = units.get(unit) if unit else None
        if unit and selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")

        zoom = nearest_zoom(zoom)
        tree = build_tree(loaded.doc.legal_units, loaded.reviews)
        decorate_for_page(tree, document_id, unit, zoom)
        context = detail_context(
            request, loaded, selected,
            tree=tree,
            v=viewer_context(loaded, page or first_page_of(selected), zoom, unit),
            progress=progress_of(loaded),
            oob=False,
        )
        return templates.TemplateResponse(request, "review.html", context)

    @app.get("/documents/{document_id}/pages/{page}", include_in_schema=False)
    def page_image(document_id: str, page: int, catalog=Depends(get_catalog)):
        loaded = load_or_404(catalog, document_id)
        if page < 1 or page > loaded.page_count:
            raise HTTPException(status_code=404, detail="Không có trang này")
        try:
            image = loaded.page_image(page)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Không render được trang {page}: {exc}")
        return FileResponse(image, media_type="image/png")

    @app.get("/documents/{document_id}/viewer", include_in_schema=False)
    def viewer(request: Request, document_id: str, page: int = 1, zoom: int = DEFAULT_ZOOM,
               unit: str | None = None, catalog=Depends(get_catalog)):
        """Khung trái (HTMX): đổi trang hoặc đổi mức phóng to."""
        loaded = load_or_404(catalog, document_id)
        context = {"v": viewer_context(loaded, page, zoom, unit), "oob": False}
        return templates.TemplateResponse(request, "partials/viewer.html", context)

    @app.get("/documents/{document_id}/units/{unit_id}", include_in_schema=False)
    def unit_detail(request: Request, document_id: str, unit_id: str, zoom: int = DEFAULT_ZOOM,
                    catalog=Depends(get_catalog)):
        """Bấm một đơn vị (HTMX): trả chi tiết, kèm khung trái đã nhảy trang (out-of-band)."""
        loaded = load_or_404(catalog, document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        context = detail_context(
            request, loaded, selected, v=viewer_context(loaded, first_page_of(selected), nearest_zoom(zoom), unit_id), oob=True
        )
        return templates.TemplateResponse(request, "unit_response.html", context)

    @app.post("/documents/{document_id}/units/{unit_id}/review", include_in_schema=False)
    def review_action(request: Request, document_id: str, unit_id: str, action: str = Form(...), reviewer: str = Form(""),
                      note: str = Form(""), ocr_reason: str = Form(""), content: str = Form(""),
                      catalog=Depends(get_catalog)):
        """Nút thẩm định (HTMX): ghi sự kiện rồi trả lại khung chi tiết, chấm màu ở cây và thanh tiến độ (out-of-band)."""
        loaded = load_or_404(catalog, document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        combined_note = note.strip()
        if action == "edit" and ocr_reason.strip():
            combined_note = f"[{ocr_reason.strip()}] {combined_note}".strip()
        errors: list[str] = []
        if not hasattr(catalog, "record_review"):
            errors = ["Thẩm định cần PostgreSQL (đặt POSTGRES_HOST hoặc DATABASE_URL)"]
        else:
            try:
                catalog.record_review(document_id, unit_id, action, reviewer, combined_note or None, content)
            except ReviewProblem as exc:
                errors = exc.problems
        if errors:
            context = detail_context(request, loaded, selected, errors=errors, form_note=note,
                                     form_content=content if action == "edit" else None, reviewer=reviewer, oob=False)
            return templates.TemplateResponse(request, "partials/detail.html", context)
        loaded = catalog.get(document_id)
        selected = loaded.units_by_id[unit_id]
        context = detail_context(request, loaded, selected, reviewer=reviewer.strip(), progress=progress_of(loaded))
        response = templates.TemplateResponse(request, "review_response.html", context)
        response.set_cookie(REVIEWER_COOKIE, quote(reviewer.strip(), safe=""), max_age=365 * 24 * 3600, samesite="lax")
        return response

    return app


app = create_app()
