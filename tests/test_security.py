"""Test hồi quy cho các lỗi bảo mật / ổn định đã sửa. Chạy ở chế độ thư mục file, không cần PostgreSQL."""
import hashlib
import hmac
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings, load_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.accounts import AccountService  # noqa: E402
from app.services.auth import (  # noqa: E402
    LEGACY_DEFAULT_ADMIN_PASSWORD,
    LoginThrottle,
    load_or_create_secret,
    sign_session,
    verify_session,
)
from app.services.store import DocumentStore  # noqa: E402
from app.web_auth import safe_next  # noqa: E402

DOC = "ND254_2026"
ADMIN_PW = "admin-pass-for-tests"
USER_PW = "user-pass-for-tests"


def make_settings(data_dir, storage, **extra):
    return Settings(data_dir=data_dir, storage_dir=storage, conninfo=None, auto_import_dir=None, **extra)


@pytest.fixture
def storage(tmp_path):
    return tmp_path / "storage"


@pytest.fixture
def client(data_dir, storage):
    app = create_app(make_settings(data_dir, storage, admin_password=ADMIN_PW), catalog=DocumentStore(data_dir))
    with TestClient(app) as test_client:  # `with` để chạy lifespan (tạo tài khoản admin)
        yield test_client


def login(client, username, password, redirect_to="/"):
    return client.post(
        "/account/login",
        data={"username": username, "password": password, "redirect_to": redirect_to},
        follow_redirects=False,
    )


def add_account(client, username, role="reviewer", approved=True):
    client.app.state.accounts.create(username, username, USER_PW, role=role, is_approved=approved)
    return username, USER_PW


def assert_sent_to_login(response):
    assert response.status_code == 303 and response.headers["location"].startswith("/login")


# ---- mật khẩu admin & khóa ký phiên: không còn giá trị mặc định trong mã nguồn ----

def test_admin_gets_random_password_when_none_configured(data_dir, storage):
    app = create_app(make_settings(data_dir, storage), catalog=DocumentStore(data_dir))
    with TestClient(app) as client:
        assert login(client, "admin", LEGACY_DEFAULT_ADMIN_PASSWORD).status_code == 401
        text = (storage / "initial_admin_password.txt").read_text(encoding="utf-8")
        password = text.split("Mật khẩu: ", 1)[1].splitlines()[0]
        assert len(password) >= 12 and password != LEGACY_DEFAULT_ADMIN_PASSWORD
        response = login(client, "admin", password)
        assert response.status_code == 303 and "session_token=" in response.headers["set-cookie"]


def test_legacy_default_admin_password_is_rotated(tmp_path):
    accounts = AccountService(DocumentStore(tmp_path))
    accounts.create("admin", "Admin", LEGACY_DEFAULT_ADMIN_PASSWORD, role="admin", is_approved=True)
    accounts.ensure_admin("admin", None, tmp_path)
    assert accounts.authenticate("admin", LEGACY_DEFAULT_ADMIN_PASSWORD) is None
    assert (tmp_path / "initial_admin_password.txt").is_file()


def test_admin_password_setting_resets_password_and_restores_role(tmp_path):
    accounts = AccountService(DocumentStore(tmp_path))
    accounts.create("admin", "Admin", "old-password-1", role="viewer", is_approved=False)
    accounts.ensure_admin("admin", "new-password-1", tmp_path)
    account = accounts.authenticate("admin", "new-password-1")
    assert account and account["role"] == "admin" and account["is_approved"]
    assert accounts.authenticate("admin", "old-password-1") is None


def test_secret_key_is_generated_once_per_storage_dir(tmp_path):
    first = load_or_create_secret(None, tmp_path)
    assert first == load_or_create_secret(None, tmp_path) and len(first) >= 32
    assert (tmp_path / ".secret_key").is_file()
    assert load_or_create_secret("configured-secret-value", tmp_path) == "configured-secret-value"
    assert load_or_create_secret(None, tmp_path / "other") != first


def test_session_tokens_are_signed_and_expire():
    secret = "k" * 40
    token = sign_session("duy", secret)
    assert verify_session(token, secret, 3600) == "duy"
    assert verify_session(token, "x" * 40, 3600) is None
    assert verify_session("admin" + token[3:], secret, 3600) is None
    assert verify_session(sign_session("duy", secret, issued_at=int(time.time()) - 7200), secret, 3600) is None
    assert verify_session("admin|" + "0" * 64, secret, 3600) is None
    assert verify_session(None, secret, 3600) is None and verify_session("", secret, 3600) is None


def test_forged_identity_cookies_and_user_agent_grant_nothing(client):
    old_secret = "review-app-secret-key-2026-drking"  # khóa từng nằm trong mã nguồn
    forged_old = "admin|" + hmac.new(old_secret.encode(), b"admin", hashlib.sha256).hexdigest()
    attempts = [
        {"reviewer": "admin"},
        {"session_token": forged_old},
        {"session_token": sign_session("admin", old_secret)},
        {"session_token": sign_session("admin", "wrong-secret-" * 4)},
    ]
    for cookies in attempts:
        header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        response = client.get("/admin/users", headers={"cookie": header, "user-agent": "testclient"}, follow_redirects=False)
        assert_sent_to_login(response)
    assert_sent_to_login(client.get("/", headers={"user-agent": "testclient"}, follow_redirects=False))


def test_basic_auth_works_and_wrong_password_is_rejected(client):
    assert client.get("/admin/users", auth=("admin", ADMIN_PW)).status_code == 200
    assert_sent_to_login(client.get("/admin/users", auth=("admin", "sai-mat-khau"), follow_redirects=False))


# ---- đăng nhập / đăng ký ----

def test_login_redirect_stays_on_this_site(client):
    for target in ("//evil.example", "https://evil.example/x", "/\\evil.example", "javascript:alert(1)", "  //evil.example"):
        response = login(client, "admin", ADMIN_PW, redirect_to=target)
        assert response.status_code == 303 and response.headers["location"] == "/", target
    inside = login(client, "admin", ADMIN_PW, redirect_to=f"/documents/{DOC}?unit=ND254_2026_D9")
    assert inside.headers["location"] == f"/documents/{DOC}?unit=ND254_2026_D9"
    page = client.get("/login", params={"next": "//evil.example"})
    assert 'value="/"' in page.text and "evil.example" not in page.text


def test_safe_next():
    assert safe_next("/documents/X?unit=Y") == "/documents/X?unit=Y"
    for bad in (None, "", "//x.y", "http://x.y", "/\\x.y", "x.y", "/a\nb", "\\\\x.y"):
        assert safe_next(bad) == "/"


def test_login_is_throttled_after_repeated_failures(client):
    for _ in range(10):
        assert login(client, "admin", "sai-mat-khau").status_code == 401
    blocked = login(client, "admin", ADMIN_PW)
    assert blocked.status_code == 429 and "thử lại sau" in blocked.text


def test_login_throttle_window():
    throttle = LoginThrottle(max_failures=2, window_seconds=60)
    throttle.failed("1.2.3.4", "Duy")
    assert throttle.retry_after("1.2.3.4", "duy") == 0
    throttle.failed("1.2.3.4", "duy")
    assert throttle.retry_after("1.2.3.4", "duy") > 0
    assert throttle.retry_after("5.6.7.8", "duy") == 0  # máy khác không bị chặn
    throttle.succeeded("1.2.3.4", "duy")
    assert throttle.retry_after("1.2.3.4", "duy") == 0


def test_registration_validates_input_and_never_overwrites_accounts(client):
    def register(username, password, display_name=""):
        return client.post(
            "/account/register",
            data={"username": username, "password": password, "display_name": display_name},
            follow_redirects=False,
        )

    assert register("ab", "12345678").status_code == 400  # tên quá ngắn
    assert register("có dấu", "12345678").status_code == 400
    assert register("nguoi_moi", "1234").status_code == 400  # mật khẩu quá ngắn
    assert register("ADMIN", "hijacked-pass").status_code == 400  # không chiếm được tài khoản admin
    assert login(client, "admin", "hijacked-pass").status_code == 401
    assert login(client, "admin", ADMIN_PW).status_code == 303

    client.cookies.clear()
    assert register("Nguoi_Moi", "matkhau-moi-1", "Người mới").status_code == 303
    account = client.app.state.accounts.get("nguoi_moi")
    assert account["role"] == "viewer" and not account["is_approved"]  # chờ Admin cấp quyền


def test_admin_pages_and_actions_require_admin(client):
    add_account(client, "khach", role="viewer", approved=False)
    assert login(client, "khach", USER_PW).status_code == 303
    assert_sent_to_login(client.get("/admin/users", follow_redirects=False))
    create = client.post("/admin/users/create", data={"username": "moi_tao", "password": "12345678", "role": "admin"})
    assert create.status_code == 403 and client.app.state.accounts.get("moi_tao") is None
    grant = client.post("/admin/users/khach/permission", data={"grant": "admin"})
    assert grant.status_code == 403 and client.app.state.accounts.get("khach")["role"] == "viewer"


def test_admin_cannot_demote_themselves(client):
    assert login(client, "admin", ADMIN_PW).status_code == 303
    response = client.post("/admin/users/admin/permission", data={"grant": "revoke"}, follow_redirects=False)
    assert response.status_code == 303 and "err=" in response.headers["location"]
    assert client.app.state.accounts.get("admin")["role"] == "admin"


# ---- thêm / xóa văn bản ----

def test_delete_rejects_path_traversal(client, data_dir):
    assert login(client, "admin", ADMIN_PW).status_code == 303
    for bad in ("%2E%2E", "%2e%2e", "%2E"):
        response = client.post(f"/documents/{bad}/delete", follow_redirects=False)  # tới được route với document_id=".."
        assert response.status_code == 404 and response.json()["detail"] == "Không có văn bản này", bad
    assert (data_dir / DOC / "document.json").is_file() and data_dir.parent.is_dir()


def test_only_admin_can_delete(client, data_dir):
    assert client.post(f"/documents/{DOC}/delete", follow_redirects=False).status_code == 303
    add_account(client, "duy")
    assert login(client, "duy", USER_PW).status_code == 303
    denied = client.post(f"/documents/{DOC}/delete", follow_redirects=False)
    assert "err=" in denied.headers["location"] and (data_dir / DOC).is_dir()

    assert login(client, "admin", ADMIN_PW).status_code == 303
    deleted = client.post(f"/documents/{DOC}/delete", follow_redirects=False)
    assert deleted.status_code == 303 and "msg=" in deleted.headers["location"]
    assert not (data_dir / DOC).exists()


def test_upload_requires_an_account_with_review_permission(client, sample_pdf, sample_json):
    files = {"pdf": ("source.pdf", sample_pdf), "document": ("document.json", sample_json)}
    assert_sent_to_login(client.post("/documents/upload", files=files, follow_redirects=False))
    add_account(client, "khach", role="viewer", approved=False)
    assert login(client, "khach", USER_PW).status_code == 303
    viewer = client.post("/documents/upload", files=files, follow_redirects=False)
    assert viewer.status_code == 303 and "err=" in viewer.headers["location"]


def test_list_page_hides_upload_and_delete_from_non_admins(client):
    add_account(client, "khach", role="viewer", approved=False)
    assert login(client, "khach", USER_PW).status_code == 303
    page = client.get("/")
    assert page.status_code == 200 and 'action="/documents/upload"' not in page.text and "/delete" not in page.text


# ---- ổn định / triển khai ----

def test_security_headers_favicon_and_healthz(client):
    response = client.get("/login")
    assert response.headers["x-frame-options"] == "DENY" and response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/favicon.ico").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok", "database": False, "db_error": False}


def test_settings_read_env_file_and_process_env_wins(tmp_path):
    from psycopg.conninfo import conninfo_to_dict

    env_file = tmp_path / ".env"
    env_file.write_text(
        '﻿# ghi chú\nPOSTGRES_HOST=db.local\nPOSTGRES_PASSWORD="p@ss w0rd"\nexport STORAGE_DIR=rel/storage\n'
        "SECRET_KEY=from-file\nSESSION_DAYS=abc\n",
        encoding="utf-8",
    )
    settings = load_settings(environ={"SECRET_KEY": "from-env"}, env_file=env_file)
    assert settings.secret_key == "from-env"
    parts = conninfo_to_dict(settings.conninfo)
    assert parts["host"] == "db.local" and parts["password"] == "p@ss w0rd"
    assert settings.storage_dir.is_absolute() and settings.storage_dir.parts[-2:] == ("rel", "storage")
    assert settings.session_max_age == 14 * 24 * 3600  # giá trị sai -> mặc định
    assert load_settings(environ={}, env_file=None).conninfo is None


def test_unreachable_database_falls_back_to_read_only(data_dir, storage):
    pytest.importorskip("psycopg_pool")
    settings = Settings(
        data_dir=data_dir, storage_dir=storage, conninfo="postgresql://u:p@127.0.0.1:1/x", auto_import_dir=None,
        admin_password=ADMIN_PW, db_connect_timeout=1,
    )
    started = time.monotonic()
    with TestClient(create_app(settings)) as client:
        assert time.monotonic() - started < 20
        page = client.get("/?guest=1")
        assert page.status_code == 200 and "Chưa kết nối được PostgreSQL" in page.text and DOC in page.text
        assert "p@" not in page.text and ":p@" not in page.text
        assert client.get("/healthz").json()["database"] is False
        assert login(client, "admin", ADMIN_PW).status_code == 303
