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

from app.services.accounts import can_review, is_admin, public
from app.services.auth import verify_session

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


def current_user(request: Request) -> dict | None:
    """Tài khoản đang đăng nhập (không kèm password_hash), hoặc None. Kết quả được nhớ trong request."""
    if getattr(request.state, "user_resolved", False):
        return request.state.user
    state = request.app.state
    accounts = getattr(state, "accounts", None)
    user = None
    if accounts is not None:
        username = verify_session(request.cookies.get(SESSION_COOKIE), state.secret_key, state.settings.session_max_age)
        if username:
            user = accounts.get(username)
        if user is None:
            creds = _basic_credentials(request)
            if creds:
                name, password = creds
                ip, throttle = client_ip(request), state.login_throttle
                if not throttle.retry_after(ip, name):
                    user = accounts.authenticate(name, password)
                    (throttle.succeeded if user else throttle.failed)(ip, name)
    request.state.user = public(user)
    request.state.user_resolved = True
    return request.state.user


def require_reviewer(request: Request) -> dict:
    """Dependency cho API ghi dữ liệu: 401 nếu chưa xác thực, 403 nếu chưa được cấp quyền soát."""
    user = current_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Cần đăng nhập bằng tài khoản có quyền soát (cookie phiên hoặc HTTP Basic: curl -u tai_khoan:mat_khau)",
            headers={"WWW-Authenticate": 'Basic realm="review-app", charset="UTF-8"'},
        )
    if not can_review(user):
        raise HTTPException(status_code=403, detail="Tài khoản chưa được Admin cấp quyền soát")
    return user


__all__ = ["SESSION_COOKIE", "LEGACY_REVIEWER_COOKIE", "client_ip", "safe_next", "current_user", "require_reviewer",
           "can_review", "is_admin"]
