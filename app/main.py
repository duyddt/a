"""FastAPI: API JSON (/api, xem /docs) và giao diện kiểu File Explorer (Jinja2 + HTMX)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import get_catalog, load_or_404
from app.api import router as api_router
from app.config import Settings, load_settings
from app.services.models import ImportProblem
from app.services.review import (
    ACTION_LABELS,
    OCR_REASONS,
    ReviewProblem,
    build_inline_diff_html,
    build_ocr_report,
    extract_char_diffs,
    progress,
    resume_unit_id,
)
from app.services.store import DocumentStore
from app.services.tree import build_tree, decorate_for_page
from app.services.views import DEFAULT_ZOOM, first_page_of, nearest_zoom, viewer_context, vn_time

BASE_DIR = Path(__file__).resolve().parent
log = logging.getLogger("review-app")
REVIEWER_COOKIE = "reviewer"

# Bộ nhớ tạm cho chế độ thư mục file (khi chưa bật PostgreSQL vẫn thử được tài khoản & tiến trình)
_FILE_MODE_ACCOUNTS: dict[str, dict] = {}
_FILE_MODE_PROGRESS: dict[tuple[str, str], dict] = {}


def progress_of(loaded) -> dict | None:
    if not loaded.can_review:
        return None
    p = progress([u.unit_id for u in loaded.doc.legal_units], loaded.reviews)
    report = build_ocr_report(loaded.doc, loaded.reviews)
    p["char_accuracy_percent"] = report["char_accuracy_percent"]
    p["word_accuracy_percent"] = report["word_accuracy_percent"]
    p["unit_accuracy_percent"] = report["unit_accuracy_percent"]
    p["structural_fidelity_percent"] = report["structural_fidelity_percent"]
    p["composite_score"] = report["composite_score"]
    p["cer_percent"] = report["cer_percent"]
    p["wer_percent"] = report["wer_percent"]
    p["edited_units"] = report["edited_units"]
    p["ocr_report"] = report
    return p


def _save_user_pos(catalog, username: str, document_id: str, unit_id: str | None, page: int) -> None:
    username = (username or "").strip()
    if not username or not unit_id:
        return
    if hasattr(catalog, "save_progress"):
        try:
            catalog.save_progress(username, document_id, unit_id, page)
            return
        except Exception:
            pass
    _FILE_MODE_ACCOUNTS.setdefault(username, {"username": username, "display_name": username})
    _FILE_MODE_PROGRESS[(username, document_id)] = {
        "username": username,
        "document_id": document_id,
        "last_unit_id": unit_id,
        "last_page": page,
    }


def _get_user_pos(catalog, username: str, document_id: str) -> dict | None:
    username = (username or "").strip()
    if not username:
        return None
    if hasattr(catalog, "get_user_progress"):
        try:
            res = catalog.get_user_progress(username, document_id)
            if res:
                return res
        except Exception:
            pass
    return _FILE_MODE_PROGRESS.get((username, document_id))


def _list_accounts(catalog) -> list[dict]:
    if hasattr(catalog, "list_accounts"):
        try:
            return catalog.list_accounts()
        except Exception:
            pass
    return list(_FILE_MODE_ACCOUNTS.values())


def create_app(settings: Settings | None = None, catalog=None) -> FastAPI:
    settings = settings or load_settings()
    use_db = catalog is None and settings.conninfo is not None
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    templates.env.filters["vn_time"] = vn_time
    templates.env.globals["ACTION_LABELS"] = ACTION_LABELS
    templates.env.globals["OCR_REASONS"] = OCR_REASONS

    def detail_context(request: Request, loaded, unit, **extra) -> dict:
        unit_review = loaded.reviews.get(unit.unit_id) if unit else None
        inline_diff = None
        char_diffs = []
        if unit and unit_review and unit_review.edit_count > 0 and unit_review.original_content is not None:
            inline_diff = build_inline_diff_html(unit_review.original_content, unit.content)
            char_diffs = extract_char_diffs(unit_review.original_content, unit.content)
        current_user = unquote(request.cookies.get(REVIEWER_COOKIE, "")).strip()
        context = {
            "doc": loaded.doc,
            "unit": unit,
            "inline_diff": inline_diff,
            "char_diffs": char_diffs,
            "can_review": loaded.can_review,
            "review": unit_review,
            "history": list(reversed(loaded.events_of(unit.unit_id))) if unit else [],
            "reviewer": current_user,
            "accounts": _list_accounts(request.app.state.catalog),
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
    app.state.importer = None
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

    @app.post("/account/login", include_in_schema=False)
    def login_account(
        request: Request,
        username: str = Form(...),
        display_name: str = Form(""),
        redirect_to: str = Form("/"),
        catalog=Depends(get_catalog),
    ):
        """Tạo hoặc chuyển tài khoản người soát và lưu vào cookie để theo dõi tiến trình riêng."""
        clean_user = username.strip()[:100]
        if clean_user:
            if hasattr(catalog, "upsert_account"):
                try:
                    catalog.upsert_account(clean_user, (display_name or clean_user).strip()[:100])
                except Exception:
                    pass
            _FILE_MODE_ACCOUNTS[clean_user] = {
                "username": clean_user,
                "display_name": (display_name or clean_user).strip()[:100],
            }
        resp = RedirectResponse(url=redirect_to or "/", status_code=303)
        if clean_user:
            resp.set_cookie(REVIEWER_COOKIE, quote(clean_user, safe=""), max_age=365 * 24 * 3600, samesite="lax")
        else:
            resp.delete_cookie(REVIEWER_COOKIE)
        return resp

    @app.get("/", include_in_schema=False)
    def document_list(request: Request, catalog=Depends(get_catalog)):
        current_user = unquote(request.cookies.get(REVIEWER_COOKIE, "")).strip()
        items = catalog.list_documents()
        user_progress_map = {}
        for item in items:
            if not item.problem and current_user:
                up = _get_user_pos(catalog, current_user, item.document_id)
                if up:
                    user_progress_map[item.document_id] = up
        return templates.TemplateResponse(
            request,
            "list.html",
            {
                "items": items,
                "reviewer": current_user,
                "accounts": _list_accounts(catalog),
                "user_progress_map": user_progress_map,
            },
        )

    @app.get("/documents/{document_id}", include_in_schema=False)
    def review(request: Request, document_id: str, unit: str | None = None, page: int | None = None,
               zoom: int = DEFAULT_ZOOM, catalog=Depends(get_catalog)):
        loaded = load_or_404(catalog, document_id)
        units = loaded.units_by_id
        current_user = unquote(request.cookies.get(REVIEWER_COOKIE, "")).strip()
        saved_pos = _get_user_pos(catalog, current_user, document_id)
        saved_unit = saved_pos["last_unit_id"] if saved_pos else None

        if unit is None and loaded.doc.legal_units:
            unit = resume_unit_id(
                [u.unit_id for u in loaded.doc.legal_units],
                loaded.reviews,
                loaded.events,
                reviewer=current_user,
                saved_unit_id=saved_unit,
            )
        selected = units.get(unit) if unit else None
        if unit and selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        zoom = nearest_zoom(zoom)
        target_page = page or first_page_of(selected)
        if current_user and selected:
            _save_user_pos(catalog, current_user, document_id, selected.unit_id, target_page)

        tree = build_tree(loaded.doc.legal_units, loaded.reviews)
        decorate_for_page(tree, document_id, unit, zoom)
        context = detail_context(
            request, loaded, selected,
            tree=tree,
            v=viewer_context(loaded, target_page, zoom, unit),
            progress=progress_of(loaded),
            saved_pos=saved_pos,
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
        loaded = load_or_404(catalog, document_id)
        context = {"v": viewer_context(loaded, page, zoom, unit), "oob": False}
        return templates.TemplateResponse(request, "partials/viewer.html", context)

    @app.get("/documents/{document_id}/units/{unit_id}", include_in_schema=False)
    def unit_detail(request: Request, document_id: str, unit_id: str, zoom: int = DEFAULT_ZOOM,
                    catalog=Depends(get_catalog)):
        loaded = load_or_404(catalog, document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        current_user = unquote(request.cookies.get(REVIEWER_COOKIE, "")).strip()
        target_page = first_page_of(selected)
        if current_user:
            _save_user_pos(catalog, current_user, document_id, unit_id, target_page)
        context = detail_context(
            request, loaded, selected, v=viewer_context(loaded, target_page, nearest_zoom(zoom), unit_id), oob=True
        )
        return templates.TemplateResponse(request, "unit_response.html", context)

    @app.post("/documents/{document_id}/units/{unit_id}/review", include_in_schema=False)
    def review_action(request: Request, document_id: str, unit_id: str, action: str = Form(...), reviewer: str = Form(""),
                      note: str = Form(""), ocr_reason: str = Form(""), content: str = Form(""),
                      catalog=Depends(get_catalog)):
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
        clean_reviewer = reviewer.strip()
        # Lưu tiến trình tiếp theo cho tài khoản vừa thao tác
        next_uid = resume_unit_id(
            [u.unit_id for u in loaded.doc.legal_units],
            loaded.reviews,
            loaded.events,
            reviewer=clean_reviewer,
            saved_unit_id=unit_id,
        ) or unit_id
        _save_user_pos(catalog, clean_reviewer, document_id, next_uid, first_page_of(loaded.units_by_id.get(next_uid)))

        context = detail_context(request, loaded, selected, reviewer=clean_reviewer, progress=progress_of(loaded))
        response = templates.TemplateResponse(request, "review_response.html", context)
        response.set_cookie(REVIEWER_COOKIE, quote(clean_reviewer, safe=""), max_age=365 * 24 * 3600, samesite="lax")
        return response

    return app


app = create_app()
