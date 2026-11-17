"""Xác thực: băm mật khẩu (PBKDF2-HMAC-SHA256), ký/kiểm tra token phiên có hạn dùng, khóa ký theo từng máy,
giới hạn số lần đăng nhập sai và kiểm tra tên tài khoản / mật khẩu.

SECRET_KEY không đặt thì tự sinh một lần và lưu ở <storage_dir>/.secret_key (giữ nguyên qua các lần khởi động).
Mật khẩu tài khoản admin lấy từ ADMIN_PASSWORD (mặc định admin123, xem app/config.py).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("review-app")

PBKDF2_ITERATIONS = 100_000
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
MIN_PASSWORD_LENGTH = 8
SECRET_FILE_NAME = ".secret_key"


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return f"{salt}${dk.hex()}"


_DUMMY_HASH = hash_password(secrets.token_hex(8))


def verify_password(password: str, stored_hash: str | None) -> bool:
    """So khớp mật khẩu. Tài khoản không tồn tại (stored_hash rỗng) vẫn tốn cùng thời gian để không lộ tên tài khoản."""
    if not stored_hash or "$" not in stored_hash:
        hash_password(password or "", _DUMMY_HASH.split("$", 1)[0])
        return False
    salt, _ = stored_hash.split("$", 1)
    expected = hash_password(password or "", salt)
    return hmac.compare_digest(expected, stored_hash)


def validate_username(username: str) -> str | None:
    """Trả về thông báo lỗi, hoặc None nếu hợp lệ."""
    if not USERNAME_RE.match(username or ""):
        return "Tên tài khoản dài 3–64 ký tự, chỉ gồm chữ không dấu, số, dấu chấm, gạch dưới, gạch ngang."
    return None


def validate_password(password: str) -> str | None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự."
    return None


# ---- khóa ký phiên ----

def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_or_create_secret(configured: str | None, storage_dir: Path) -> str:
    """Khóa ký phiên: lấy từ cấu hình; không có thì đọc/tạo <storage_dir>/.secret_key."""
    if configured:
        if len(configured) < 16:
            log.warning("SECRET_KEY quá ngắn (< 16 ký tự); nên dùng chuỗi ngẫu nhiên dài hơn.")
        return configured
    path = Path(storage_dir) / SECRET_FILE_NAME
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if len(existing) >= 32:
            return existing
    except (FileNotFoundError, PermissionError, OSError):
        pass
    secret = secrets.token_urlsafe(48)
    try:
        _write_private(path, secret)
    except OSError as exc:
        log.warning("Không ghi được %s (%s). Phiên đăng nhập sẽ mất khi khởi động lại.", path, exc)
    return secret


# ---- token phiên: "<username>|<thời điểm tạo>|<chữ ký>" ----

def _signature(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_session(username: str, secret: str, issued_at: int | None = None) -> str:
    issued_at = int(time.time()) if issued_at is None else issued_at
    payload = f"{username}|{issued_at}"
    return f"{payload}|{_signature(secret, payload)}"


def verify_session(token: str | None, secret: str, max_age: int) -> str | None:
    """Trả về username nếu token đúng chữ ký và còn hạn; ngược lại None."""
    if not token or token.count("|") < 2:
        return None
    username, issued, sig = token.rsplit("|", 2)
    if not username or not issued.isdigit():
        return None
    if not hmac.compare_digest(sig, _signature(secret, f"{username}|{issued}")):
        return None
    age = time.time() - int(issued)
    if age > max_age or age < -300:
        return None
    return username


# ---- giới hạn đăng nhập sai ----

class LoginThrottle:
    """Chặn tạm thời sau quá nhiều lần đăng nhập sai cho cùng (địa chỉ IP, tên tài khoản)."""

    def __init__(self, max_failures: int = 10, window_seconds: int = 15 * 60):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: tuple[str, str], now: float) -> deque[float]:
        q = self._failures.setdefault(key, deque())
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def retry_after(self, ip: str, username: str) -> int:
        """Số giây còn phải chờ; 0 nếu được thử tiếp."""
        key, now = (ip, (username or "").lower()), time.time()
        with self._lock:
            q = self._recent(key, now)
            if len(q) < self.max_failures:
                return 0
            return max(1, int(self.window - (now - q[0])))

    def failed(self, ip: str, username: str) -> None:
        key, now = (ip, (username or "").lower()), time.time()
        with self._lock:
            self._recent(key, now).append(now)
            if len(self._failures) > 10_000:  # chặn rò rỉ bộ nhớ khi bị dò nhiều tên
                for k in [k for k, q in self._failures.items() if not q]:
                    del self._failures[k]

    def succeeded(self, ip: str, username: str) -> None:
        with self._lock:
            self._failures.pop((ip, (username or "").lower()), None)
