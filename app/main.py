"""FastAPI: API JSON (/api, xem /docs) và giao diện kiểu File Explorer có Xác thực & Phân quyền Admin."""
from __future__ import annotations

import logging
import math
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import MAX_UPLOAD_BYTES, get_catalog, load_or_404, sees_every_version
from app.api import router as api_router
from app.config import DEFAULT_ADMIN_PASSWORD, Settings, load_settings
from app.services.accounts import ROLE_LABELS, ROLES, AccountService, can_review, is_active, is_admin
from app.services.auth import (
    LoginThrottle,
    load_or_create_secret,
    sign_session,
    validate_password,
    validate_username,
)
from app.services.categories import group_into_folders
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
from app.services.tree import build_tree, decorate_for_page, person_marks
from app.services.views import (
    DEFAULT_ZOOM,
    FIT,
    first_page_of,
    nearest_zoom,
    unit_versions,
    viewer_context,
    vn_time,
)
from app.services.workflow import ACTION_LABELS as WORKFLOW_LABELS
from app.services.workflow import EDITOR, STATUS_LABELS, available_actions, can_act_problem
from app.web_auth import (
    BASIC_CHALLENGE,
    LEGACY_REVIEWER_COOKIE,
    LOGIN_HINT,
    SESSION_COOKIE,
    client_ip,
    current_user,
    is_public_path,
    safe_next,
)

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
    templates.env.globals["ROLE_LABELS"] = ROLE_LABELS

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
        everyone = sees_every_version(user)
        versions = None
        if unit and everyone and loaded.tracks is not None:
            versions = [v for v in unit_versions(loaded, unit.unit_id, True) if v["version"] != "final"]
            original = loaded.original_text.get(unit.unit_id, "")
            for v in versions:
                v["diff_html"] = build_inline_diff_html(original, v["content"]) if v["content"] != original else None
            if len(versions) < 2:  # chưa người chỉnh sửa nào thao tác trên đơn vị: không cần so sánh
                versions = None
        context = {
            "doc": loaded.doc,
            "unit": unit,
            "versions": versions,
            "version_label": ("Bản chốt" if loaded.version == "final" else "Bản của bạn") if loaded.can_review else None,
            "can_edit_text": not user or user.get("role") != "approver",  # người phê duyệt duyệt và ghi chú, không sửa chữ
            "can_export": everyone and loaded.can_review,
            "inline_diff": inline_diff,
            "char_diffs": char_diffs,
            "can_review": _user_can_review(loaded, user),
            # phân công: người chưa được giao / đã bấm Hoàn thành / văn bản chưa chuyển sang phê duyệt chỉ xem
            "blocked": can_act_problem(loaded.workflow, user) if loaded.can_review and is_active(user) else None,
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
                app.state.importer = Importer(pool)
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
    async def require_login(request: Request, call_next):
        """Không còn khách xem nhanh: chưa đăng nhập thì trang web về /login, /api trả 401 (mời HTTP Basic)."""
        if is_public_path(request.url.path) or await run_in_threadpool(current_user, request) is not None:
            return await call_next(request)
        if request.url.path.startswith("/api"):
            return JSONResponse(status_code=401, content={"detail": LOGIN_HINT}, headers=BASIC_CHALLENGE)
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        login_url = f"/login?next={quote(safe_next(target), safe='')}"
        if request.headers.get("hx-request"):  # yêu cầu HTMX (khung con): cho cả trang về đăng nhập
            return JSONResponse(status_code=401, content={"detail": LOGIN_HINT}, headers={"HX-Redirect": login_url})
        return RedirectResponse(url=login_url, status_code=303)

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

    # ---- ĐĂNG NHẬP / ĐĂNG XUẤT / QUẢN TRỊ TÀI KHOẢN (chỉ Admin tạo tài khoản) ----

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
        role_val = role if role in ROLES else "reviewer"
        if accounts.exists(clean_user) or accounts.create(
            clean_user, display_name or clean_user, password, role=role_val, is_approved=True
        ) is None:
            return RedirectResponse(url=f"/admin/users?err={quote(f'Tài khoản {clean_user} đã tồn tại')}", status_code=303)
        return RedirectResponse(url=f"/admin/users?msg=Đã+tạo+tài+khoản+{quote(clean_user)}", status_code=303)

    @app.post("/admin/users/{target_user}/permission", include_in_schema=False)
    def admin_toggle_permission(request: Request, target_user: str, grant: str = Form(...)):
        user = current_user(request)
        if not is_admin(user):
            raise HTTPException(status_code=403, detail="Chỉ Admin mới có quyền cấp phép")
        if target_user == user["username"]:
            return RedirectResponse(url="/admin/users?err=Không+thể+tự+đổi+quyền+hoặc+khóa+chính+mình", status_code=303)
        target = app.state.accounts.get(target_user)
        if target is None:
            return RedirectResponse(url=f"/admin/users?err=Không+có+tài+khoản+{quote(target_user)}", status_code=303)
        # grant: một vai trò (giữ trạng thái khóa/mở) hoặc lock / unlock (giữ vai trò)
        if grant in ROLES:
            role, active = grant, bool(target["is_approved"])
        elif grant in ("lock", "unlock"):
            role, active = target["role"], grant == "unlock"
        else:
            return RedirectResponse(url="/admin/users?err=Thao+tác+không+hợp+lệ", status_code=303)
        app.state.accounts.set_permission(target_user, role, active)
        return RedirectResponse(url=f"/admin/users?msg=Đã+cập+nhật+tài+khoản+{quote(target_user)}", status_code=303)

    # ---- THỐNG KÊ (Admin) ----

    @app.get("/admin/stats", include_in_schema=False)
    def admin_stats_page(request: Request, folder: str = "", catalog=Depends(get_catalog)):
        """Tiến độ từng người (đã hoàn thành / được giao) và từng văn bản."""
        user = current_user(request)
        if not is_admin(user):
            return RedirectResponse(url="/login?next=/admin/stats&err=Cần+đăng+nhập+tài+khoản+Admin", status_code=303)
        if not hasattr(catalog, "stats"):
            return RedirectResponse(url="/?err=" + quote("Thống kê cần PostgreSQL"), status_code=303)
        categories = catalog.list_categories()
        current = next((c for c in categories if c.code == folder), None) if folder else None
        return templates.TemplateResponse(request, "admin_stats.html", {
            "current_user": user, "stats": catalog.stats(current.code if current else None),
            "categories": categories, "current": current,
            "category_names": {c.code: c.label for c in categories}, "STATUS_LABELS": STATUS_LABELS,
        })

    # ---- PHÂN CÔNG (Admin) ----

    def _assign_redirect(folder: str, msg: str = "", err: str = "") -> RedirectResponse:
        query = [f"folder={quote(folder)}"] if folder else []
        query += [f"msg={quote(msg)}"] if msg else []
        query += [f"err={quote(err)}"] if err else []
        return RedirectResponse(url="/admin/assign" + ("?" + "&".join(query) if query else ""), status_code=303)

    @app.get("/admin/assign", include_in_schema=False)
    def admin_assign_page(request: Request, folder: str = "", msg: str = "", err: str = "", catalog=Depends(get_catalog)):
        """Giao văn bản (từng văn bản hoặc cả thư mục) cho người chỉnh sửa và người phê duyệt."""
        user = current_user(request)
        if not is_admin(user):
            return RedirectResponse(url="/login?next=/admin/assign&err=Cần+đăng+nhập+tài+khoản+Admin", status_code=303)
        if not hasattr(catalog, "record_workflow"):
            return RedirectResponse(url="/?err=" + quote("Phân công cần PostgreSQL"), status_code=303)
        items = [item for item in catalog.list_documents() if item.problem is None]
        folders, _ = group_into_folders(items, catalog.list_categories())
        current = next((f for f in folders if f.category.code == folder), None) if folder else None
        accounts = sorted((a for a in app.state.accounts.list() if a.get("is_approved")), key=lambda a: a["username"])
        return templates.TemplateResponse(request, "admin_assign.html", {
            "current_user": user, "folders": folders, "current": current,
            "documents": current.documents if current else items,
            "editors": [a for a in accounts if a["role"] == "reviewer"],
            "approvers": [a for a in accounts if a["role"] == "approver"],
            "STATUS_LABELS": STATUS_LABELS, "msg": msg, "err": err,
        })

    @app.post("/admin/assign", include_in_schema=False)
    def admin_assign(request: Request, documents: list[str] = Form([]), folder: str = Form(""),
                     whole_folder: str = Form(""), editors: list[str] = Form([]), approver: str = Form(""),
                     catalog=Depends(get_catalog)):
        user = current_user(request)
        if not is_admin(user):
            raise HTTPException(status_code=403, detail="Chỉ Admin mới phân công")
        targets = list(documents)
        if whole_folder and folder:
            items = [item for item in catalog.list_documents() if item.problem is None]
            folders, _ = group_into_folders(items, catalog.list_categories())
            current = next((f for f in folders if f.category.code == folder), None)
            targets = [d.document_id for d in current.documents] if current else []
        if not targets:
            return _assign_redirect(folder, err="Chọn ít nhất một văn bản, hoặc bấm Giao cả thư mục")
        if not editors and not approver:
            return _assign_redirect(folder, err="Chọn người chỉnh sửa hoặc người phê duyệt để giao")
        done, problems = 0, []
        for document_id in dict.fromkeys(targets):
            try:
                state = catalog.workflow(document_id)
                wanted = [(name, EDITOR) for name in editors if not state.is_editor(name)]
                if approver and state.approver != approver:
                    wanted.append((approver, "approver"))
                for name, role in wanted:  # người đã được giao thì bỏ qua: giao lại cả thư mục không báo lỗi
                    catalog.record_workflow(document_id, "assign", user, target_user=name, target_role=role)
                    done += 1
            except KeyError:
                problems.append(f"Không có văn bản {document_id}")
            except ReviewProblem as exc:
                problems += [f"{document_id}: {p}" for p in exc.problems]
        return _assign_redirect(folder, msg=f"Đã giao {done} lượt cho {len(set(targets))} văn bản",
                                err="; ".join(dict.fromkeys(problems)))

    @app.post("/admin/assign/remove", include_in_schema=False)
    def admin_unassign(request: Request, document_id: str = Form(...), username: str = Form(...),
                       role: str = Form(EDITOR), folder: str = Form(""), catalog=Depends(get_catalog)):
        user = current_user(request)
        if not is_admin(user):
            raise HTTPException(status_code=403, detail="Chỉ Admin mới phân công")
        try:
            catalog.record_workflow(document_id, "unassign", user, target_user=username, target_role=role)
        except KeyError:
            return _assign_redirect(folder, err=f"Không có văn bản {document_id}")
        except ReviewProblem as exc:
            return _assign_redirect(folder, err="; ".join(exc.problems))
        return _assign_redirect(folder, msg=f"Đã gỡ {username} khỏi {document_id}")

    @app.post("/admin/assign/reopen", include_in_schema=False)
    def admin_reopen(request: Request, document_id: str = Form(...), username: str = Form(""), folder: str = Form(""),
                     catalog=Depends(get_catalog)):
        """Mở lại văn bản đã phê duyệt, hoặc cho một người chỉnh sửa đã bấm Hoàn thành sửa tiếp."""
        user = current_user(request)
        if not is_admin(user):
            raise HTTPException(status_code=403, detail="Chỉ Admin mới mở lại văn bản")
        try:
            catalog.record_workflow(document_id, "reopen", user, target_user=username or None)
        except KeyError:
            return _assign_redirect(folder, err=f"Không có văn bản {document_id}")
        except ReviewProblem as exc:
            return _assign_redirect(folder, err="; ".join(exc.problems))
        return _assign_redirect(folder, msg=f"Đã mở lại {document_id}" + (f" cho {username}" if username else ""))

    # ---- THÊM / XÓA VĂN BẢN ----

    def list_redirect(err: str = "", msg: str = "", folder: str = "") -> RedirectResponse:
        """Về màn hình danh sách (về đúng thư mục `folder` nếu có) kèm thông báo."""
        query = f"err={quote(err)}" if err else f"msg={quote(msg)}"
        place = f"folder={quote(folder.strip())}&" if folder.strip() else ""
        return RedirectResponse(url=f"/?{place}{query}", status_code=303)

    @app.post("/documents/upload", include_in_schema=False)
    def upload_document_ui(request: Request, pdf: UploadFile = File(...), document: UploadFile | None = File(None),
                           replace: bool = Form(False), category: str = Form("")):
        from app.services.importer import AlreadyImported
        from app.services.pdf_parser import auto_build_json_from_pdf

        user = current_user(request)
        if user is None:
            return RedirectResponse(url="/login?next=/&err=Cần+đăng+nhập+để+thêm+văn+bản", status_code=303)
        if not is_admin(user):
            return list_redirect(err="Chỉ Admin mới được thêm văn bản vào hệ thống", folder=category)
        importer = app.state.importer
        if importer is None:
            return list_redirect(err="Cần kết nối PostgreSQL để thêm văn bản", folder=category)
        pdf_bytes = pdf.file.read(MAX_UPLOAD_BYTES + 1)
        json_bytes = b""
        if document is not None and getattr(document, "filename", ""):
            json_bytes = document.file.read(MAX_UPLOAD_BYTES + 1)
        if len(pdf_bytes) > MAX_UPLOAD_BYTES or len(json_bytes) > MAX_UPLOAD_BYTES:
            return list_redirect(err="Tệp quá lớn (tối đa 50 MB mỗi tệp)", folder=category)
        words: dict = {}  # toạ độ chữ của các trang phải OCR, lưu lại để tô sáng trên bản scan
        try:
            if not json_bytes.strip():
                json_bytes = auto_build_json_from_pdf(pdf_bytes, pdf.filename or "document.pdf", words_out=words)
            res = importer.import_bytes(pdf_bytes, json_bytes, replace=replace, category=category.strip() or None,
                                        page_words=words)
        except AlreadyImported as exc:
            return list_redirect(err=f"Văn bản {exc.document_id} đã tồn tại với nội dung khác (Admin có thể chọn 'Ghi đè')", folder=category)
        except ImportProblem as exc:
            return list_redirect(err="; ".join(exc.problems[:3]), folder=category)
        except Exception:
            log.exception("Lỗi khi tải lên văn bản %s", pdf.filename)
            return list_redirect(err="Không xử lý được tệp tải lên (PDF hỏng hoặc không tách được cấu trúc)", folder=category)
        return RedirectResponse(url=f"/documents/{quote(res.document_id)}" + (f"?folder={quote(category.strip())}" if category.strip() else ""), status_code=303)

    @app.post("/documents/{document_id}/delete", include_in_schema=False)
    def delete_document_ui(request: Request, document_id: str, folder: str = Form(""), catalog=Depends(get_catalog)):
        if not is_admin(current_user(request)):
            return list_redirect(err="Chỉ Admin mới có quyền xóa văn bản", folder=folder)
        if not DOC_ID_RE.match(document_id):  # chặn "..", "." và mọi đường dẫn ra ngoài thư mục dữ liệu
            raise HTTPException(status_code=404, detail="Không có văn bản này")
        try:
            catalog.get(document_id)
        except KeyError:
            return list_redirect(err=f"Không có văn bản {document_id}", folder=folder)
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
            return list_redirect(err=f"Không xóa được văn bản {document_id}", folder=folder)
        return list_redirect(msg=f"Đã xóa văn bản {document_id}", folder=folder)

    # ---- CÁC TRANG CHÍNH ----

    @app.get("/", include_in_schema=False)
    def document_list(request: Request, folder: str = "", msg: str = "", err: str = "", catalog=Depends(get_catalog)):
        """Danh sách văn bản dạng cây thư mục như bộ dữ liệu: gốc là các danh mục, ?folder=I là văn bản của danh mục I."""
        user = current_user(request)  # luôn có: middleware require_login đã chặn người chưa đăng nhập
        username = user["username"]
        items = catalog.list_documents(viewer=user)  # tiến độ theo bản của người xem
        folders, loose = group_into_folders(items, catalog.list_categories())
        my_work = queue = None
        if hasattr(catalog, "record_workflow") and is_active(user):
            flows = [item for item in items if item.problem is None and item.workflow is not None]
            if user["role"] == "reviewer":
                my_work = [item for item in flows if item.workflow.is_editor(username)]
            else:  # người phê duyệt: văn bản đã chuyển sang và giao cho mình (hoặc chưa giao ai); Admin: mọi văn bản chờ
                queue = [item for item in flows if item.workflow.status == "pending"
                         and (user["role"] == "admin" or item.workflow.is_approver_for(username))]
        current = None
        if folder:
            current = next((f for f in folders if f.category.code == folder), None)
            if current is None:
                return RedirectResponse(url=f"/?err={quote(f'Không có danh mục {folder}')}", status_code=303)
        listed = current.documents if current else loose
        user_progress_map = {}
        if username:
            for item in listed:
                if not item.problem:
                    saved = app.state.accounts.get_progress(username, item.document_id)
                    if saved:
                        user_progress_map[item.document_id] = saved
        return templates.TemplateResponse(
            request,
            "list.html",
            {
                "items": items,
                "folders": folders,
                "loose": loose,
                "current": current,
                "listed": listed,
                "category_names": {f.category.code: f.category.label for f in folders},
                "current_user": user,
                "reviewer": username,
                "user_progress_map": user_progress_map,
                "my_work": my_work,
                "queue": queue,
                "msg": msg,
                "err": err,
                "can_upload": app.state.importer is not None and is_admin(user),
                "can_replace": is_admin(user),
                "db_error": app.state.db_error,
            },
        )

    @app.get("/documents/{document_id}", include_in_schema=False)
    def review(request: Request, document_id: str, unit: str | None = None, page: int | None = None,
               zoom: int = DEFAULT_ZOOM, folder: str = "", msg: str = "", err: str = "", catalog=Depends(get_catalog)):
        """`folder`: thư mục mở văn bản ra (để nút quay lại về đúng thư mục đó)."""
        user = current_user(request)
        loaded = load_or_404(catalog, document_id, user)
        units = loaded.units_by_id
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

        tree = build_tree(loaded.doc.legal_units, loaded.reviews,
                          person_marks(loaded) if sees_every_version(user) else None)
        decorate_for_page(tree, document_id, unit)
        context = detail_context(
            request, loaded, selected,
            tree=tree,
            v=viewer_context(loaded, target_page, zoom, unit),
            progress=progress_of(loaded),
            saved_pos=saved_pos,
            db_error=app.state.db_error,
            oob=False,
        )
        context["folder"] = next((c for c in catalog.list_categories() if c.code == folder), None) if folder else None
        context.update(
            wf=loaded.workflow,
            wf_can=available_actions(loaded.workflow, user),
            people_colors={person: loaded.color_of(person) for person in loaded.people},
            msg=msg, err=err,
        )
        return templates.TemplateResponse(request, "review.html", context)

    @app.post("/documents/{document_id}/workflow", include_in_schema=False)
    def document_workflow(request: Request, document_id: str, action: str = Form(...), note: str = Form(""),
                          unit: str = Form(""), catalog=Depends(get_catalog)):
        """Nút Hoàn thành / Trả lại / Hoàn tất phê duyệt / Mở lại trên trang văn bản."""
        user = current_user(request)
        back = f"/documents/{quote(document_id)}" + (f"?unit={quote(unit)}&" if unit else "?")
        if not hasattr(catalog, "record_workflow"):
            return RedirectResponse(url=back + "err=" + quote("Phân công và phê duyệt cần PostgreSQL"), status_code=303)
        if not is_active(user):
            return RedirectResponse(url=back + "err=" + quote("Tài khoản đã bị khóa"), status_code=303)
        if action not in ("submit", "return", "finalize", "reopen"):
            return RedirectResponse(url=back + "err=" + quote("Thao tác không hợp lệ"), status_code=303)
        try:
            catalog.record_workflow(document_id, action, user, note)
        except KeyError:
            raise HTTPException(status_code=404, detail="Không có văn bản này") from None
        except ReviewProblem as exc:
            return RedirectResponse(url=back + "err=" + quote("; ".join(exc.problems)), status_code=303)
        return RedirectResponse(url=back + "msg=" + quote(f"Đã {WORKFLOW_LABELS[action].lower()} văn bản"), status_code=303)

    @app.get("/documents/{document_id}/pages/{page}", include_in_schema=False)
    def page_image(document_id: str, page: int, catalog=Depends(get_catalog)):
        loaded = load_or_404(catalog, document_id)
        if page < 1 or page > loaded.page_count:
            raise HTTPException(status_code=404, detail="Không có trang này")
        try:
            image = loaded.page_image(page)
        except Exception:
            log.exception("Không render được trang %s của %s", page, document_id)
            raise HTTPException(status_code=500, detail=f"Không render được trang {page}") from None
        return FileResponse(image, media_type="image/png")

    @app.get("/documents/{document_id}/viewer", include_in_schema=False)
    def viewer(request: Request, document_id: str, page: int = 1, zoom: int = DEFAULT_ZOOM,
               unit: str | None = None, catalog=Depends(get_catalog)):
        loaded = load_or_404(catalog, document_id, current_user(request))
        context = {"v": viewer_context(loaded, page, zoom, unit), "oob": False}
        return templates.TemplateResponse(request, "partials/viewer.html", context)

    @app.get("/documents/{document_id}/units/{unit_id}", include_in_schema=False)
    def unit_detail(request: Request, document_id: str, unit_id: str, catalog=Depends(get_catalog)):
        user = current_user(request)
        loaded = load_or_404(catalog, document_id, user)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")
        target_page = first_page_of(selected)
        if user:
            app.state.accounts.save_progress(user["username"], document_id, unit_id, target_page)
        context = detail_context(
            # chọn đơn vị trong cây: khung trái hiện trọn trang đầu của đơn vị, đúng tỉ lệ, kèm vùng tô vàng
            request, loaded, selected, v=viewer_context(loaded, target_page, FIT, unit_id), oob=True
        )
        return templates.TemplateResponse(request, "unit_response.html", context)

    @app.post("/documents/{document_id}/units/{unit_id}/review", include_in_schema=False)
    def review_action(request: Request, document_id: str, unit_id: str, action: str = Form(...),
                      note: str = Form(""), ocr_reason: str = Form(""), content: str = Form(""),
                      source: str = Form(""), catalog=Depends(get_catalog)):
        user = current_user(request)
        loaded = load_or_404(catalog, document_id, user)
        selected = loaded.units_by_id.get(unit_id)
        if selected is None:
            raise HTTPException(status_code=404, detail="Không có đơn vị này")

        reviewer = user["username"] if user else ""
        combined_note = note.strip()
        if action == "edit" and ocr_reason.strip():
            combined_note = f"[{ocr_reason.strip()}] {combined_note}".strip()
        errors: list[str] = []
        if not hasattr(catalog, "record_review"):
            errors = ["Thẩm định cần PostgreSQL (đặt POSTGRES_HOST hoặc DATABASE_URL)"]
        elif not is_active(user):
            errors = ["Tài khoản của bạn đang bị Admin khóa nên không thao tác được."]
        else:
            try:
                catalog.record_review(document_id, unit_id, action, reviewer, combined_note or None, content,
                                      role=user["role"], source=source.strip() or None)
            except ReviewProblem as exc:
                errors = exc.problems
        if errors:
            context = detail_context(request, loaded, selected, errors=errors, form_note=note,
                                     form_content=content if action == "edit" else None, oob=False)
            return templates.TemplateResponse(request, "partials/detail.html", context)
        loaded = catalog.get(document_id, viewer=user)
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
