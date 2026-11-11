"""Xác thực mật khẩu (PBKDF2-HMAC-SHA256) và ký token phiên đăng nhập."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

_SECRET_KEY = os.environ.get("SECRET_KEY", "review-app-secret-key-2026-drking")
DEFAULT_ADMIN_USER = os.environ.get("ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "admin123")


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    salt, _ = stored_hash.split("$", 1)
    expected = hash_password(password, salt)
    return hmac.compare_digest(expected, stored_hash)


def sign_session(username: str) -> str:
    sig = hmac.new(_SECRET_KEY.encode("utf-8"), username.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{username}|{sig}"


def verify_session(token: str | None) -> str | None:
    if not token or "|" not in token:
        return None
    username, sig = token.rsplit("|", 1)
    expected = hmac.new(_SECRET_KEY.encode("utf-8"), username.encode("utf-8"), hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig, expected):
        return username
    return None
