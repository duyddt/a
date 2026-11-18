"""Xác định người dùng hiện tại cho trang web và REST API.

Chỉ tin hai nguồn:
- cookie phiên `session_token` đã ký bằng khóa của máy chủ và còn hạn (trình duyệt, sau khi đăng nhập);
- header `Authorization: Basic ...` (công cụ dòng lệnh, ví dụ `curl -u user:matkhau`).
Không tin cookie `reviewer`, tên người soát tự gõ hay User-Agent.
"""
from __future__ import annotations

import base64
import binascii
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from app.services.accounts import can_review, is_active, is_admin, public
from app.services.auth import session_username, sign_session, verify_session

SESSION_COOKIE = "session_token"
LEGACY_REVIEWER_COOKIE = "reviewer"  # cookie cũ, chỉ còn được xóa khi đăng nhập/đăng xuất


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def safe_next(url: str | None, default: str = "/") -> str:
    """Chỉ cho chuyển hướng tới đường dẫn trong cùng trang web (chặn open redirect như //evil.com, https://...)."""
    url = (url or "").strip()
    if not url.startswith("/") or url.startswith("//") or "\\" in url:
        return default
    if any(ord(ch) < 32 for ch in url):
        return default
    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        return default
    return url


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic" or not value:
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    username, sep, password = decoded.partition(":")
    return (username.strip(), password) if sep and username.strip() else None


def session_token_for(account: dict, secret: str) -> str:
    """Token phiên mới cho `account` (cần password_hash: đổi mật khẩu thì token cũ hết hiệu lực)."""
    return sign_session(account["username"], secret, stamp=account.get("password_hash") or "")


def current_user(request: Request) -> dict | None:
    """Tài khoản đang đăng nhập và đang hoạt động (không kèm password_hash), hoặc None. Kết quả được nhớ trong request.

    Tài khoản bị khóa coi như chưa đăng nhập: cookie phiên và HTTP Basic của nó đều bị từ chối."""
    if getattr(request.state, "user_resolved", False):
        return request.state.user
    state = request.app.state
    accounts = getattr(state, "accounts", None)
    user = None
    if accounts is not None:
        token = request.cookies.get(SESSION_COOKIE)
        candidate = accounts.get(session_username(token) or "")
        if candidate and verify_session(token, state.secret_key, state.settings.session_max_age,
                                        stamp=candidate.get("password_hash") or ""):
            user = candidate
        if user is None:
            creds = _basic_credentials(request)
            if creds:
                name, password = creds
                ip, throttle = client_ip(request), state.login_throttle
                if not throttle.retry_after(ip, name):
                    user = accounts.authenticate(name, password)
                    (throttle.succeeded if user else throttle.failed)(ip, name)
        if user is not None and not is_active(user):
            user = None
    request.state.user = public(user)
    request.state.user_resolved = True
    return request.state.user


BASIC_CHALLENGE = {"WWW-Authenticate": 'Basic realm="review-app", charset="UTF-8"'}
LOGIN_HINT = "Cần đăng nhập (cookie phiên hoặc HTTP Basic: curl -u tai_khoan:mat_khau)"

# Đường dẫn xem được khi chưa đăng nhập; mọi đường dẫn khác (trang web lẫn /api) đều cần tài khoản.
# Tài liệu API (/docs, /redoc, /openapi.json) cũng cần đăng nhập vì là tool nội bộ.
PUBLIC_PATHS = frozenset({"/login", "/account/login", "/account/logout", "/healthz", "/favicon.ico"})


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith("/static/")


def require_user(request: Request) -> dict:
    """Dependency: 401 kèm lời mời HTTP Basic nếu chưa đăng nhập."""
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail=LOGIN_HINT, headers=BASIC_CHALLENGE)
    return user


def require_reviewer(request: Request) -> dict:
    """Dependency cho API ghi dữ liệu: 401 nếu chưa xác thực (kể cả tài khoản bị khóa)."""
    user = require_user(request)
    if not can_review(user):
        raise HTTPException(status_code=403, detail="Tài khoản đang bị Admin khóa nên không thao tác được")
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Chỉ Admin mới làm được việc này")
    return user


__all__ = ["session_token_for", "SESSION_COOKIE", "LEGACY_REVIEWER_COOKIE", "BASIC_CHALLENGE", "LOGIN_HINT", "client_ip", "safe_next",
           "current_user", "is_public_path", "require_user", "require_reviewer", "require_admin", "can_review", "is_active",
           "is_admin"]
