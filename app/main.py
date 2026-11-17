"""FastAPI: API JSON (/api, xem /docs) và giao diện kiểu File Explorer có Xác thực & Phân quyền Admin."""
from __future__ import annotations

import logging
import math
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import MAX_UPLOAD_BYTES, get_catalog, load_or_404
from app.api import router as api_router
from app.config import DEFAULT_ADMIN_PASSWORD, Settings, load_settings
from app.services.accounts import AccountService, can_review, is_admin
from app.services.auth import LoginThrottle, load_or_create_secret, sign_session, validate_password, validate_username
from app.services.models import DOC_ID_RE, ImportProblem
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
from app.web_auth import LEGACY_REVIEWER_COOKIE, SESSION_COOKIE, client_ip, current_user, safe_next

BASE_DIR = Path(__file__).resolve().parent
log = logging.getLogger("review-app")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "frame-ancestors 'none'",
}


def _configure_logging() -> None:
    """Log của ứng dụng (mật khẩu admin khởi tạo, lỗi kết nối DB...) luôn hiện ra console, kể cả khi chạy uvicorn trần."""
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     [review-app] %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False


def _describe_conninfo(conninfo: str) -> str:
    """Mô tả chuỗi kết nối để ghi log mà không lộ mật khẩu."""
    try:
        from psycopg.conninfo import conninfo_to_dict

        parts = conninfo_to_dict(conninfo)
        return f"{parts.get('user', '?')}@{parts.get('host', 'localhost')}:{parts.get('port', 5432)}/{parts.get('dbname', '?')}"
    except Exception:
        return "(chuỗi kết nối không đọc được)"


def _user_can_review(loaded, user: dict | None) -> bool:
    """Thẩm định được khi văn bản nằm trong PostgreSQL và người dùng là Admin hoặc người soát đã được cấp quyền."""
    return bool(loaded.can_review) and can_review(user)


def progress_of(loaded) -> dict | None:
    if not loaded.can_review:
        return None
    p = progress([u.unit_id for u in loaded.doc.legal_units], loaded.reviews)
    report = build_ocr_report(loaded.doc, loaded.reviews)
    for key in ("char_accuracy_percent", "word_accuracy_percent", "unit_accuracy_percent", "structural_fidelity_percent",
                "composite_score", "cer_percent", "wer_percent", "edited_units"):
        p[key] = report[key]
    p["ocr_report"] = report
    return p


def create_app(settings: Settings | None = None, catalog=None) -> FastAPI:
    _configure_logging()
    settings = settings or load_settings()
    use_db = catalog is None and settings.conninfo is not None
    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    templates.env.filters["vn_time"] = vn_time
    templates.env.globals["ACTION_LABELS"] = ACTION_LABELS
    templates.env.globals["OCR_REASONS"] = OCR_REASONS

    def set_catalog(app: FastAPI, new_catalog) -> None:
        app.state.catalog = new_catalog
        app.state.accounts = AccountService(new_catalog) if new_catalog is not None else None

    def ensure_admin(app: FastAPI) -> None:
        app.state.accounts.ensure_admin(settings.admin_username, settings.admin_password)
        if settings.admin_password == DEFAULT_ADMIN_PASSWORD:
            log.warning(
                "Tài khoản quản trị: %s / %s (mật khẩu mặc định, chỉ dùng để chạy thử trên máy cá nhân). "
                "Khi cho người khác truy cập, hãy đặt ADMIN_PASSWORD trong .env.",
                settings.admin_username, DEFAULT_ADMIN_PASSWORD,
            )

    def detail_context(request: Request, loaded, unit, **extra) -> dict:
        unit_review = loaded.reviews.get(unit.unit_id) if unit else None
        inline_diff = None
        char_diffs = []
        if unit and unit_review and unit_review.edit_count > 0 and unit_review.original_content is not None:
            inline_diff = build_inline_diff_html(unit_review.original_content, unit.content)
            char_diffs = extract_char_diffs(unit_review.original_content, unit.content)
        user = current_user(request)
        context = {
            "doc": loaded.doc,
            "unit": unit,
            "inline_diff": inline_diff,
            "char_diffs": char_diffs,
            "can_review": _user_can_review(loaded, user),
            "db_ready": loaded.can_review,
            "current_user": user,
            "review": unit_review,
            "history": list(reversed(loaded.events_of(unit.unit_id))) if unit else [],
            "reviewer": user["username"] if user else "",
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

            try:
                pool = create_pool(settings.conninfo, wait_seconds=settings.db_connect_timeout)
                for name in migrate(pool):
                    log.info("Đã áp dụng migration %s", name)
            except Exception as exc:
                if pool is not None:
                    pool.close()
                    pool = None
                where = _describe_conninfo(settings.conninfo)
                app.state.db_error = f"Không kết nối được PostgreSQL ({where}): {type(exc).__name__}"
                log.error(
                    "%s (%s). Ứng dụng tạm chạy ở chế độ CHỈ XEM, đọc văn bản từ %s. Kiểm tra PostgreSQL đã chạy và "
                    "DATABASE_URL/POSTGRES_* trong .env rồi khởi động lại.", app.state.db_error, exc, settings.data_dir,
                )
                set_catalog(app, DocumentStore(settings.data_dir))
                ensure_admin(app)
            else:
                set_catalog(app, DbCatalog(pool, settings.storage_dir))
                app.state.importer = Importer(pool, settings.storage_dir)
                ensure_admin(app)
                if settings.auto_import_dir:
                    try:
                        for name, outcome in app.state.importer.import_all(settings.auto_import_dir):
                            log.info("Nhập tự động %s: %s", name, outcome)
                    except Exception:
                        log.exception("Lỗi khi nhập tự động từ %s (bỏ qua, ứng dụng vẫn chạy)", settings.auto_import_dir)
        elif app.state.accounts is not None:  # chế độ thư mục file: tài khoản nằm trong bộ nhớ
            ensure_admin(app)
        try:
            yield
        finally:
            if pool is not None:
                pool.close()

    app = FastAPI(title="Web app thẩm định dữ liệu pháp lý", lifespan=lifespan)
    app.state.settings = settings
    app.state.secret_key = load_or_create_secret(settings.secret_key, settings.storage_dir)
    app.state.login_throttle = LoginThrottle()
    app.state.db_error = None
    app.state.importer = None  # chế độ DB gán trong lifespan
    set_catalog(app, catalog or (None if use_db else DocumentStore(settings.data_dir)))
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.include_router(api_router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

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

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(BASE_DIR / "static" / "favicon.svg", media_type="image/svg+xml")

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        """Kiểm tra sống (Docker HEALTHCHECK, giám sát). `database` = false khi đang chạy chế độ chỉ xem."""
        return {"status": "ok", "database": app.state.importer is not None, "db_error": app.state.db_error is not None}

    # ---- ĐĂNG NHẬP / ĐĂNG KÝ / ĐĂNG XUẤT / QUẢN TRỊ ADMIN ----

    def login_page_response(request: Request, next_url: str, err: str = "", msg: str = "", status_code: int = 200):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": safe_next(next_url), "msg": msg, "err": err, "current_user": current_user(request)},
            status_code=status_code,
        )

    def signed_in_redirect(request: Request, username: str, redirect_to: str) -> RedirectResponse:
        resp = RedirectResponse(url=safe_next(redirect_to), status_code=303)
        resp.set_cookie(
            SESSION_COOKIE, sign_session(username, app.state.secret_key), max_age=settings.session_max_age,
            httponly=True, samesite="lax", secure=request.url.scheme == "https",
        )
        resp.delete_cookie(LEGACY_REVIEWER_COOKIE)
        return resp

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request, next: str = "/", msg: str = "", err: str = ""):
        return login_page_response(request, next, err=err, msg=msg)

    @app.post("/account/login", include_in_schema=False)
    def login_submit(request: Request, username: str = Form(...), password: str = Form(""), redirect_to: str = Form("/")):
        clean_user = username.strip()[:100]
        ip, throttle = client_ip(request), app.state.login_throttle
        wait = throttle.retry_after(ip, clean_user)
        if wait:
            err = f"Đăng nhập sai quá nhiều lần. Vui lòng thử lại sau {math.ceil(wait / 60)} phút."
            return login_page_response(request, redirect_to, err=err, status_code=429)
        acc = app.state.accounts.authenticate(clean_user, password) if app.state.accounts else None
        if acc is None:
            throttle.failed(ip, clean_user)
            return login_page_response(request, redirect_to, err="Sai tên tài khoản hoặc mật khẩu!", status_code=401)
        throttle.succeeded(ip, clean_user)
        return signed_in_redirect(request, acc["username"], redirect_to)

    @app.post("/account/register", include_in_schema=False)
    def register_submit(request: Request, username: str = Form(...), display_name: str = Form(""), password: str = Form(...),
                        redirect_to: str = Form("/")):
        clean_user = username.strip().lower()
        problem = validate_username(clean_user) or validate_password(password)
        if problem:
            return login_page_response(request, redirect_to, err=problem, status_code=400)
        accounts = app.state.accounts
        if accounts.exists(clean_user):
            return login_page_response(request, redirect_to, status_code=400,
                                       err=f"Tài khoản '{clean_user}' đã tồn tại, vui lòng chọn tên khác hoặc đăng nhập.")
        # Người dùng tự tạo tài khoản -> role='viewer', is_approved=False (chỉ xem, chờ Admin duyệt)
        if accounts.create(clean_user, display_name or clean_user, password, role="viewer", is_approved=False) is None:
            return login_page_response(request, redirect_to, status_code=400,
                                       err=f"Tài khoản '{clean_user}' đã tồn tại, vui lòng chọn tên khác hoặc đăng nhập.")
        return signed_in_redirect(request, clean_user, redirect_to)

    @app.post("/account/logout", include_in_schema=False)
    def logout_submit():
        resp = RedirectResponse(url="/login?msg=Đã+đăng+xuất", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        resp.delete_cookie(LEGACY_REVIEWER_COOKIE)
        return resp

    @app.get("/admin/users", include_in_schema=False)
    def admin_users_page(request: Request, msg: str = "", err: str = ""):
        user = current_user(request)
        if not is_admin(user):
            return RedirectResponse(url="/login?next=/admin/users&err=Cần+đăng+nhập+tài+khoản+Admin", status_code=303)
        return templates.TemplateResponse(
            request, "admin_users.html", {"current_user": user, "accounts": app.state.accounts.list(), "msg": msg, "err": err},
        )

    @app.post("/admin/users/create", include_in_schema=False)
    def admin_create_user(request: Request, username: str = Form(...), display_name: str = Form(""),
                          password: str = Form(...), role: str = Form("reviewer")):
        if not is_admin(current_user(request)):
            raise HTTPException(status_code=403, detail="Chỉ Admin mới có quyền tạo tài khoản")
        clean_user = username.strip().lower()
        problem = validate_username(clean_user) or validate_password(password)
        if problem:
            return RedirectResponse(url=f"/admin/users?err={quote(problem)}", status_code=303)
        accounts = app.state.accounts
        role_val = role if role in ("admin", "reviewer", "viewer") else "reviewer"
        if accounts.exists(clean_user) or accounts.create(
            clean_user, display_name or clean_user, password, role=role_val, is_approved=role_val in ("admin", "reviewer")
        ) is None:
            return RedirectResponse(url=f"/admin/users?err={quote(f'Tài khoản {clean_user} đã tồn tại')}", status_code=303)
        return RedirectResponse(url=f"/admin/users?msg=Đã+tạo+tài+khoản+{quote(clean_user)}", status_code=303)

    @app.post("/admin/users/{target_user}/permission", include_in_schema=False)
    def admin_toggle_permission(request: Request, target_user: str, grant: str = Form(...)):
        user = current_user(request)
        if not is_admin(user):
            raise HTTPException(status_code=403, detail="Chỉ Admin mới có quyền cấp phép")
        if target_user == user["username"]:
            return RedirectResponse(url="/admin/users?err=Không+thể+tự+đổi+quyền+của+chính+mình", status_code=303)
        role, approved = {"approve": ("reviewer", True), "admin": ("admin", True)}.get(grant, ("viewer", False))
        if not app.state.accounts.set_permission(target_user, role, approved):
            return RedirectResponse(url=f"/admin/users?err=Không+có+tài+khoản+{quote(target_user)}", status_code=303)
        return RedirectResponse(url=f"/admin/users?msg=Đã+cập+nhật+quyền+cho+{quote(target_user)}", status_code=303)

    # ---- THÊM / XÓA VĂN BẢN ----

    def list_redirect(err: str = "", msg: str = "") -> RedirectResponse:
        query = f"err={quote(err)}" if err else f"msg={quote(msg)}"
        return RedirectResponse(url=f"/?{query}", status_code=303)

    @app.post("/documents/upload", include_in_schema=False)
    def upload_document_ui(request: Request, pdf: UploadFile = File(...), document: UploadFile | None = File(None),
                           replace: bool = Form(False)):
        from app.services.importer import AlreadyImported
        from app.services.pdf_parser import auto_build_json_from_pdf

        user = current_user(request)
        if user is None:
            return RedirectResponse(url="/login?next=/&err=Cần+đăng+nhập+để+thêm+văn+bản", status_code=303)
        if not can_review(user):
            return list_redirect(err="Tài khoản chưa được Admin cấp quyền soát nên chưa thêm được văn bản")
        if replace and not is_admin(user):
            return list_redirect(err="Chỉ Admin mới được ghi đè văn bản đã có")
        importer = app.state.importer
        if importer is None:
            return list_redirect(err="Cần kết nối PostgreSQL để thêm văn bản")
        pdf_bytes = pdf.file.read(MAX_UPLOAD_BYTES + 1)
        json_bytes = b""
        if document is not None and getattr(document, "filename", ""):
            json_bytes = document.file.read(MAX_UPLOAD_BYTES + 1)
        if len(pdf_bytes) > MAX_UPLOAD_BYTES or len(json_bytes) > MAX_UPLOAD_BYTES:
            return list_redirect(err="Tệp quá lớn (tối đa 50 MB mỗi tệp)")
        try:
            if not json_bytes.strip():
                json_bytes = auto_build_json_from_pdf(pdf_bytes, pdf.filename or "document.pdf")
            res = importer.import_bytes(pdf_bytes, json_bytes, replace=replace)
        except AlreadyImported as exc:
            return list_redirect(err=f"Văn bản {exc.document_id} đã tồn tại với nội dung khác (Admin có thể chọn 'Ghi đè')")
        except ImportProblem as exc:
            return list_redirect(err="; ".join(exc.problems[:3]))
        except Exception:
            log.exception("Lỗi khi tải lên văn bản %s", pdf.filename)
            return list_redirect(err="Không xử lý được tệp tải lên (PDF hỏng hoặc không tách được cấu trúc)")
        return RedirectResponse(url=f"/documents/{quote(res.document_id)}", status_code=303)

    @app.post("/documents/{document_id}/delete", include_in_schema=False)
    def delete_document_ui(request: Request, document_id: str, catalog=Depends(get_catalog)):
        if not is_admin(current_user(request)):
            return list_redirect(err="Chỉ Admin mới có quyền xóa văn bản")
        if not DOC_ID_RE.match(document_id):  # chặn "..", "." và mọi đường dẫn ra ngoài thư mục dữ liệu
            raise HTTPException(status_code=404, detail="Không có văn bản này")
        try:
            catalog.get(document_id)
        except KeyError:
            return list_redirect(err=f"Không có văn bản {document_id}")
        except ImportProblem:
            pass  # văn bản lỗi cấu trúc vẫn được xóa
        in_db = hasattr(catalog, "pool")
        base = Path(catalog.storage_dir if in_db else catalog.data_dir).resolve()
        target = (base / document_id).resolve()
        if target.parent != base:
            raise HTTPException(status_code=404, detail="Không có văn bản này")
        try:
            if in_db:
                from app import repository

                with catalog.pool.connection() as conn:
                    repository.delete_document(conn, document_id)
                    conn.execute("DELETE FROM reviewer_progress WHERE document_id = %s", (document_id,))
            shutil.rmtree(target, ignore_errors=True)  # Windows có thể khóa file ảnh đang mở; phần còn lại xóa sau
            if hasattr(catalog, "_cache"):
                catalog._cache.pop(document_id, None)
        except Exception:
            log.exception("Lỗi khi xóa văn bản %s", document_id)
            return list_redirect(err=f"Không xóa được văn bản {document_id}")
        return list_redirect(msg=f"Đã xóa văn bản {document_id}")

    # ---- CÁC TRANG CHÍNH ----

    @app.get("/", include_in_schema=False)
    def document_list(request: Request, guest: int = 0, msg: str = "", err: str = "", catalog=Depends(get_catalog)):
        user = current_user(request)
        if user is None and not guest:  # chưa đăng nhập -> trang đăng nhập (hoặc ?guest=1 để xem nhanh)
            return RedirectResponse(url="/login?next=/", status_code=303)
        username = user["username"] if user else ""
        items = catalog.list_documents()
        user_progress_map = {}
        if username:
            for item in items:
                if not item.problem:
                    saved = app.state.accounts.get_progress(username, item.document_id)
                    if saved:
                        user_progress_map[item.document_id] = saved
        return templates.TemplateResponse(
            request,
            "list.html",
            {
                "items": items,
                "current_user": user,
                "reviewer": username,
                "user_progress_map": user_progress_map,
                "msg": msg,
                "err": err,
                "can_upload": app.state.importer is not None and can_review(user),
                "can_replace": is_admin(user),
                "db_error": app.state.db_error,
            },
        )

    @app.get("/documents/{document_id}", include_in_schema=False)
    def review(request: Request, document_id: str, unit: str | None = None, page: int | None = None,
               zoom: int = DEFAULT_ZOOM, catalog=Depends(get_catalog)):
        loaded = load_or_404(catalog, document_id)
        units = loaded.units_by_id
        user = current_user(request)
        username = user["username"] if user else ""
        saved_pos = app.state.accounts.get_progress(username, document_id) if username else None
        saved_unit = saved_pos["last_unit_id"] if saved_pos else None

        if unit is None and loaded.doc.legal_units:
            unit = resume_unit_id(
                [u.unit_id for u in loaded.doc.legal_units],
                loaded.reviews,
                loaded.events,
                reviewer=username,
                saved_unit_id=saved_unit,
            )
        selected = units.get(unit) if unit else None
        if unit and selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        zoom = nearest_zoom(zoom)
        target_page = page or first_page_of(selected)
        if username and selected:
            app.state.accounts.save_progress(username, document_id, selected.unit_id, target_page)

        tree = build_tree(loaded.doc.legal_units, loaded.reviews)
        decorate_for_page(tree, document_id, unit, zoom)
        context = detail_context(
            request, loaded, selected,
            tree=tree,
            v=viewer_context(loaded, target_page, zoom, unit),
            progress=progress_of(loaded),
            saved_pos=saved_pos,
            db_error=app.state.db_error,
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
        except Exception:
            log.exception("Không render được trang %s của %s", page, document_id)
            raise HTTPException(status_code=500, detail=f"Không render được trang {page}")
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
        user = current_user(request)
        target_page = first_page_of(selected)
        if user:
            app.state.accounts.save_progress(user["username"], document_id, unit_id, target_page)
        context = detail_context(
            request, loaded, selected, v=viewer_context(loaded, target_page, nearest_zoom(zoom), unit_id), oob=True
        )
        return templates.TemplateResponse(request, "unit_response.html", context)

    @app.post("/documents/{document_id}/units/{unit_id}/review", include_in_schema=False)
    def review_action(request: Request, document_id: str, unit_id: str, action: str = Form(...),
                      note: str = Form(""), ocr_reason: str = Form(""), content: str = Form(""),
                      catalog=Depends(get_catalog)):
        loaded = load_or_404(catalog, document_id)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")

        user = current_user(request)
        reviewer = user["username"] if user else ""
        combined_note = note.strip()
        if action == "edit" and ocr_reason.strip():
            combined_note = f"[{ocr_reason.strip()}] {combined_note}".strip()
        errors: list[str] = []
        if not hasattr(catalog, "record_review"):
            errors = ["Thẩm định cần PostgreSQL (đặt POSTGRES_HOST hoặc DATABASE_URL)"]
        elif user is None:
            errors = ["Cần đăng nhập bằng tài khoản có quyền soát để thẩm định."]
        elif not _user_can_review(loaded, user):
            errors = ["Tài khoản của bạn đang ở chế độ Chỉ xem (chờ Admin cấp quyền soát)."]
        else:
            try:
                catalog.record_review(document_id, unit_id, action, reviewer, combined_note or None, content)
            except ReviewProblem as exc:
                errors = exc.problems
        if errors:
            context = detail_context(request, loaded, selected, errors=errors, form_note=note,
                                     form_content=content if action == "edit" else None, oob=False)
            return templates.TemplateResponse(request, "partials/detail.html", context)
        loaded = catalog.get(document_id)
        selected = loaded.units_by_id[unit_id]
        next_uid = resume_unit_id(
            [u.unit_id for u in loaded.doc.legal_units],
            loaded.reviews,
            loaded.events,
            reviewer=reviewer,
            saved_unit_id=unit_id,
        ) or unit_id
        app.state.accounts.save_progress(reviewer, document_id, next_uid, first_page_of(loaded.units_by_id.get(next_uid)))
        context = detail_context(request, loaded, selected, progress=progress_of(loaded))
        return templates.TemplateResponse(request, "review_response.html", context)

    return app


app = create_app()
