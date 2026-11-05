"""FastAPI + Jinja2 + HTMX: Web app thẩm định dữ liệu pháp lý (Trợ lý AI Thuế - Kế toán)."""
from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import StructuralErrorType, get_db, init_db
from app.services.auth import get_all_users, get_current_user, set_user_cookie
from app.services.review_service import (
    approve_unit,
    edit_unit_text,
    export_parser_feedback,
    export_verified_document,
    flag_unit,
    get_all_unit_statuses,
    get_document_progress,
    get_unit_history,
    get_unit_status,
    report_structural_error,
)
from app.services.store import DocumentStore, ImportProblem, LoadedDocument
from app.services.views import DEFAULT_ZOOM, build_rows, first_page_of, nearest_zoom, viewer_context

BASE_DIR = Path(__file__).resolve().parent


# --- Pydantic Schemas cho REST API ---
class EditTextRequest(BaseModel):
    new_content: str


class FlagRequest(BaseModel):
    note: str


class StructuralErrorRequest(BaseModel):
    error_type: StructuralErrorType
    note: str = ""


def create_app(data_dir: str | Path | None = None, db_engine=None) -> FastAPI:
    data_path = Path(data_dir or os.environ.get("DATA_DIR") or BASE_DIR.parent / "data")

    # Nếu truyền data_dir riêng mà không truyền db_engine thì dùng SQLite trong data_dir đó
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    if db_engine is None and data_dir is not None:
        test_db_path = data_path / "review.db"
        db_engine = create_engine(f"sqlite:///{test_db_path}", connect_args={"check_same_thread": False})

    init_db(target_engine=db_engine)

    store = DocumentStore(data_path)
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

    app = FastAPI(title="Web app thẩm định dữ liệu pháp lý (AI Thuế - Kế toán)")
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    if db_engine is not None:
        custom_session_local = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

        def override_get_db():
            db = custom_session_local()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db

    def load(document_id: str) -> LoadedDocument:
        """Lỗi 404 nếu không có văn bản."""
        try:
            return store.get(document_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Không có văn bản này")

    @app.exception_handler(ImportProblem)
    async def import_problem_handler(request: Request, exc: ImportProblem):
        return templates.TemplateResponse(request, "import_error.html", {"problem": exc}, status_code=422)

    # --- 1. Chuyển đổi tài khoản thẩm định viên ---
    @app.get("/switch-user/{username}")
    def switch_user(username: str, request: Request, response: Response):
        referer = request.headers.get("referer") or "/"
        resp = RedirectResponse(url=referer, status_code=303)
        set_user_cookie(resp, username)
        return resp

    # --- 2. Màn hình danh sách văn bản và tiến độ ---
    @app.get("/")
    def document_list(request: Request, db: Session = Depends(get_db)):
        current_user = get_current_user(db, request)
        users = get_all_users(db)
        items = []
        for document_id in store.list_ids():
            try:
                loaded = store.get(document_id)
                progress = get_document_progress(db, document_id, len(loaded.doc.legal_units))
                items.append({
                    "document_id": document_id,
                    "loaded": loaded,
                    "progress": progress,
                    "problem": None,
                })
            except ImportProblem as problem:
                items.append({
                    "document_id": document_id,
                    "loaded": None,
                    "progress": None,
                    "problem": problem,
                })
        return templates.TemplateResponse(
            request,
            "list.html",
            {
                "items": items,
                "current_user": current_user,
                "users": users,
            },
        )

    # --- 3. Màn hình soát hai khung ---
    @app.get("/documents/{document_id}")
    def review(
        request: Request,
        document_id: str,
        unit: str | None = None,
        page: int | None = None,
        zoom: int = DEFAULT_ZOOM,
        db: Session = Depends(get_db),
    ):
        loaded = load(document_id)
        current_user = get_current_user(db, request)
        users = get_all_users(db)

        all_statuses = get_all_unit_statuses(db, document_id, loaded.units_by_id)
        progress = get_document_progress(db, document_id, len(loaded.doc.legal_units))

        if unit is None and loaded.doc.legal_units:
            unit = loaded.doc.legal_units[0].unit_id

        selected = loaded.units_by_id.get(unit) if unit else None
        if unit and selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")

        zoom = nearest_zoom(zoom)
        unit_status = all_statuses.get(unit) if unit else None

        context = {
            "doc": loaded.doc,
            "unit": selected,
            "status": unit_status,
            "rows": build_rows(loaded, unit, zoom, all_statuses),
            "v": viewer_context(loaded, page or first_page_of(selected), zoom, unit),
            "progress": progress,
            "current_user": current_user,
            "users": users,
            "oob": False,
        }
        return templates.TemplateResponse(request, "review.html", context)

    # --- 4. Render ảnh từng trang PDF ---
    @app.get("/documents/{document_id}/pages/{page}")
    def page_image(document_id: str, page: int):
        loaded = load(document_id)
        if page < 1 or page > loaded.page_count:
            raise HTTPException(status_code=404, detail="Không có trang này")
        try:
            image = loaded.page_image(page)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Không render được trang {page}: {exc}")
        return FileResponse(image, media_type="image/png")

    # --- 5. Khung xem PDF (HTMX) ---
    @app.get("/documents/{document_id}/viewer")
    def viewer(request: Request, document_id: str, page: int = 1, zoom: int = DEFAULT_ZOOM, unit: str | None = None):
        loaded = load(document_id)
        context = {"v": viewer_context(loaded, page, zoom, unit), "oob": False}
        return templates.TemplateResponse(request, "partials/viewer.html", context)

    # --- 6. Chọn đơn vị trên cây (HTMX) ---
    @app.get("/documents/{document_id}/units/{unit_id}")
    def unit_detail(
        request: Request,
        document_id: str,
        unit_id: str,
        zoom: int = DEFAULT_ZOOM,
        db: Session = Depends(get_db),
    ):
        loaded = load(document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")

        current_user = get_current_user(db, request)
        all_statuses = get_all_unit_statuses(db, document_id, loaded.units_by_id)
        progress = get_document_progress(db, document_id, len(loaded.doc.legal_units))
        zoom = nearest_zoom(zoom)

        unit_status = all_statuses.get(unit_id)

        context = {
            "doc": loaded.doc,
            "unit": selected,
            "status": unit_status,
            "rows": build_rows(loaded, unit_id, zoom, all_statuses),
            "v": viewer_context(loaded, first_page_of(selected), zoom, unit_id),
            "progress": progress,
            "current_user": current_user,
            "oob": True,
        }
        return templates.TemplateResponse(request, "unit_response.html", context)

    # --- 7. Thao tác 1: DUYỆT ĐƠN VỊ ---
    @app.post("/documents/{document_id}/units/{unit_id}/approve")
    def action_approve(
        request: Request,
        document_id: str,
        unit_id: str,
        zoom: int = Form(DEFAULT_ZOOM),
        db: Session = Depends(get_db),
    ):
        loaded = load(document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")

        current_user = get_current_user(db, request)
        approve_unit(db, document_id, unit_id, current_user.id)

        all_statuses = get_all_unit_statuses(db, document_id, loaded.units_by_id)
        progress = get_document_progress(db, document_id, len(loaded.doc.legal_units))
        zoom = nearest_zoom(zoom)

        context = {
            "doc": loaded.doc,
            "unit": selected,
            "status": all_statuses.get(unit_id),
            "rows": build_rows(loaded, unit_id, zoom, all_statuses),
            "v": viewer_context(loaded, first_page_of(selected), zoom, unit_id),
            "progress": progress,
            "current_user": current_user,
            "oob": True,
        }
        return templates.TemplateResponse(request, "unit_response.html", context)

    # --- 8. Thao tác 2: SỬA CHỮ (APPEND-ONLY) ---
    @app.post("/documents/{document_id}/units/{unit_id}/edit")
    def action_edit(
        request: Request,
        document_id: str,
        unit_id: str,
        new_content: str = Form(...),
        zoom: int = Form(DEFAULT_ZOOM),
        db: Session = Depends(get_db),
    ):
        loaded = load(document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")

        current_user = get_current_user(db, request)
        edit_unit_text(db, document_id, unit_id, current_user.id, new_content, selected.content)

        all_statuses = get_all_unit_statuses(db, document_id, loaded.units_by_id)
        progress = get_document_progress(db, document_id, len(loaded.doc.legal_units))
        zoom = nearest_zoom(zoom)

        context = {
            "doc": loaded.doc,
            "unit": selected,
            "status": all_statuses.get(unit_id),
            "rows": build_rows(loaded, unit_id, zoom, all_statuses),
            "v": viewer_context(loaded, first_page_of(selected), zoom, unit_id),
            "progress": progress,
            "current_user": current_user,
            "oob": True,
        }
        return templates.TemplateResponse(request, "unit_response.html", context)

    # --- 9. Thao tác 3: GẮN CỜ "KHÔNG CHẮC" ---
    @app.post("/documents/{document_id}/units/{unit_id}/flag")
    def action_flag(
        request: Request,
        document_id: str,
        unit_id: str,
        note: str = Form(...),
        zoom: int = Form(DEFAULT_ZOOM),
        db: Session = Depends(get_db),
    ):
        loaded = load(document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")

        current_user = get_current_user(db, request)
        flag_unit(db, document_id, unit_id, current_user.id, note)

        all_statuses = get_all_unit_statuses(db, document_id, loaded.units_by_id)
        progress = get_document_progress(db, document_id, len(loaded.doc.legal_units))
        zoom = nearest_zoom(zoom)

        context = {
            "doc": loaded.doc,
            "unit": selected,
            "status": all_statuses.get(unit_id),
            "rows": build_rows(loaded, unit_id, zoom, all_statuses),
            "v": viewer_context(loaded, first_page_of(selected), zoom, unit_id),
            "progress": progress,
            "current_user": current_user,
            "oob": True,
        }
        return templates.TemplateResponse(request, "unit_response.html", context)

    # --- 10. Thao tác 4: BÁO LỖI CẤU TRÚC ---
    @app.post("/documents/{document_id}/units/{unit_id}/structural-error")
    def action_structural_error(
        request: Request,
        document_id: str,
        unit_id: str,
        error_type: StructuralErrorType = Form(...),
        note: str = Form(""),
        zoom: int = Form(DEFAULT_ZOOM),
        db: Session = Depends(get_db),
    ):
        loaded = load(document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")

        current_user = get_current_user(db, request)
        report_structural_error(db, document_id, unit_id, current_user.id, error_type, note)

        all_statuses = get_all_unit_statuses(db, document_id, loaded.units_by_id)
        progress = get_document_progress(db, document_id, len(loaded.doc.legal_units))
        zoom = nearest_zoom(zoom)

        context = {
            "doc": loaded.doc,
            "unit": selected,
            "status": all_statuses.get(unit_id),
            "rows": build_rows(loaded, unit_id, zoom, all_statuses),
            "v": viewer_context(loaded, first_page_of(selected), zoom, unit_id),
            "progress": progress,
            "current_user": current_user,
            "oob": True,
        }
        return templates.TemplateResponse(request, "unit_response.html", context)

    # --- 11. Xem lịch sử sửa đổi & quyết định ---
    @app.get("/documents/{document_id}/units/{unit_id}/history")
    def unit_history(
        request: Request,
        document_id: str,
        unit_id: str,
        db: Session = Depends(get_db),
    ):
        loaded = load(document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")

        history = get_unit_history(db, document_id, unit_id, selected.content)
        return templates.TemplateResponse(request, "partials/history_modal.html", {"history": history})

    # --- 12. Xuất kết quả (File ZIP gồm JSON và Parser Feedback) ---
    @app.get("/documents/{document_id}/export")
    def export_document_zip(document_id: str, db: Session = Depends(get_db)):
        loaded = load(document_id)
        verified_doc = export_verified_document(db, loaded)
        feedback = export_parser_feedback(db, loaded)

        doc_json = json.dumps(verified_doc.model_dump(mode="json"), ensure_ascii=False, indent=2)
        feedback_json = json.dumps(feedback, ensure_ascii=False, indent=2)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{document_id}_verified.json", doc_json)
            zf.writestr(f"{document_id}_parser_feedback.json", feedback_json)

        buffer.seek(0)
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{document_id}_export.zip"'},
        )

    # --- 13. REST APIs theo đặc tả kỹ thuật ---
    @app.post("/api/documents/{document_id}/units/{unit_id}/approve")
    def api_approve(
        request: Request,
        document_id: str,
        unit_id: str,
        db: Session = Depends(get_db),
    ):
        loaded = load(document_id)
        if unit_id not in loaded.units_by_id:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        current_user = get_current_user(db, request)
        dec = approve_unit(db, document_id, unit_id, current_user.id)
        return {
            "status": "success",
            "document_id": document_id,
            "unit_id": unit_id,
            "decision": dec.decision.value,
            "reviewer": current_user.username,
            "updated_at": dec.created_at.isoformat(),
        }

    @app.post("/api/documents/{document_id}/units/{unit_id}/edit-text")
    def api_edit_text(
        request: Request,
        document_id: str,
        unit_id: str,
        body: EditTextRequest,
        db: Session = Depends(get_db),
    ):
        loaded = load(document_id)
        unit = loaded.units_by_id.get(unit_id)
        if unit is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        current_user = get_current_user(db, request)
        rev = edit_unit_text(db, document_id, unit_id, current_user.id, body.new_content, unit.content)
        return {
            "status": "success",
            "document_id": document_id,
            "unit_id": unit_id,
            "revision_id": rev.id,
            "content_before": rev.content_before,
            "content_after": rev.content_after,
            "reviewer": current_user.username,
            "updated_at": rev.created_at.isoformat(),
        }

    @app.post("/api/documents/{document_id}/units/{unit_id}/flag")
    def api_flag(
        request: Request,
        document_id: str,
        unit_id: str,
        body: FlagRequest,
        db: Session = Depends(get_db),
    ):
        loaded = load(document_id)
        if unit_id not in loaded.units_by_id:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        current_user = get_current_user(db, request)
        dec = flag_unit(db, document_id, unit_id, current_user.id, body.note)
        return {
            "status": "success",
            "document_id": document_id,
            "unit_id": unit_id,
            "decision": dec.decision.value,
            "note": dec.note,
            "reviewer": current_user.username,
            "updated_at": dec.created_at.isoformat(),
        }

    @app.post("/api/documents/{document_id}/units/{unit_id}/structural-error")
    def api_structural_error(
        request: Request,
        document_id: str,
        unit_id: str,
        body: StructuralErrorRequest,
        db: Session = Depends(get_db),
    ):
        loaded = load(document_id)
        if unit_id not in loaded.units_by_id:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        current_user = get_current_user(db, request)
        dec = report_structural_error(db, document_id, unit_id, current_user.id, body.error_type, body.note)
        return {
            "status": "success",
            "document_id": document_id,
            "unit_id": unit_id,
            "decision": dec.decision.value,
            "error_type": dec.structural_error_type.value,
            "note": dec.note,
            "reviewer": current_user.username,
            "updated_at": dec.created_at.isoformat(),
        }

    @app.get("/api/documents/{document_id}/units/{unit_id}/history")
    def api_history(
        document_id: str,
        unit_id: str,
        db: Session = Depends(get_db),
    ):
        loaded = load(document_id)
        unit = loaded.units_by_id.get(unit_id)
        if unit is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        return get_unit_history(db, document_id, unit_id, unit.content)

    @app.get("/api/documents/{document_id}/progress")
    def api_progress(
        document_id: str,
        db: Session = Depends(get_db),
    ):
        loaded = load(document_id)
        return get_document_progress(db, document_id, len(loaded.doc.legal_units))

    return app


app = create_app()
