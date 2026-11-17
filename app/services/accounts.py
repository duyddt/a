"""Tài khoản, phân quyền và vị trí đang soát của từng người.

Chế độ PostgreSQL: lưu trong bảng reviewer_account / reviewer_progress (qua DbCatalog).
Chế độ thư mục file: lưu trong bộ nhớ của tiến trình (mất khi khởi động lại; chế độ này không lưu được thẩm định).
"""
from __future__ import annotations

import logging
import threading

from .auth import hash_password, verify_password

log = logging.getLogger("review-app")

ROLES = ("admin", "reviewer", "viewer")


def is_admin(user: dict | None) -> bool:
    return bool(user) and user.get("role") == "admin"


def can_review(user: dict | None) -> bool:
    """Admin, hoặc người soát đã được Admin cấp quyền."""
    if not user:
        return False
    return user.get("role") == "admin" or (user.get("role") == "reviewer" and bool(user.get("is_approved")))


def public(account: dict | None) -> dict | None:
    """Bản sao không có password_hash, an toàn để đưa vào template."""
    if account is None:
        return None
    return {k: v for k, v in account.items() if k != "password_hash"}


class AccountService:
    def __init__(self, catalog):
        self.catalog = catalog
        self.persistent = hasattr(catalog, "get_account")
        self._accounts: dict[str, dict] = {}
        self._progress: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()

    # ---- tài khoản ----

    def get(self, username: str) -> dict | None:
        username = (username or "").strip()
        if not username:
            return None
        if self.persistent:
            return self.catalog.get_account(username)
        with self._lock:
            acc = self._accounts.get(username)
            return dict(acc) if acc else None

    def authenticate(self, username: str, password: str) -> dict | None:
        """Tài khoản nếu đúng mật khẩu; None nếu sai (thời gian xử lý như nhau dù tài khoản có hay không)."""
        acc = self.get(username)
        if acc is None and username and username != username.lower():
            acc = self.get(username.lower())
        ok = verify_password(password, acc.get("password_hash") if acc else None)
        return acc if ok and acc else None

    def create(self, username: str, display_name: str, password: str, role: str = "viewer",
               is_approved: bool = False) -> dict | None:
        """Tạo tài khoản mới. Trả None nếu tên đã có (không ghi đè)."""
        username = username.strip()
        role = role if role in ROLES else "viewer"
        pw_hash = hash_password(password)
        name = (display_name or username).strip()[:100]
        if self.persistent:
            return self.catalog.create_account(username, name, pw_hash, role=role, is_approved=is_approved)
        with self._lock:
            if username in self._accounts:
                return None
            acc = {"username": username, "display_name": name, "password_hash": pw_hash,
                   "role": role, "is_approved": is_approved}
            self._accounts[username] = acc
            return public(acc)

    def exists(self, username: str) -> bool:
        """Có tài khoản trùng tên (không phân biệt hoa thường) hay không."""
        username = (username or "").strip()
        if self.get(username) is not None or self.get(username.lower()) is not None:
            return True
        return any(a["username"].lower() == username.lower() for a in self.list())

    def set_permission(self, username: str, role: str, is_approved: bool) -> bool:
        role = role if role in ROLES else "viewer"
        if self.persistent:
            return self.catalog.update_account_permission(username, role, is_approved) is not None
        with self._lock:
            acc = self._accounts.get(username)
            if acc is None:
                return False
            acc["role"], acc["is_approved"] = role, is_approved
            return True

    def set_password(self, username: str, password: str) -> None:
        pw_hash = hash_password(password)
        if self.persistent:
            self.catalog.update_account_password(username, pw_hash)
            return
        with self._lock:
            if username in self._accounts:
                self._accounts[username]["password_hash"] = pw_hash

    def list(self) -> list[dict]:
        if self.persistent:
            return [public(a) for a in self.catalog.list_accounts()]
        with self._lock:
            return [public(a) for a in self._accounts.values()]

    # ---- admin mặc định (chạy một lần khi khởi động) ----

    def ensure_admin(self, username: str, password: str) -> None:
        """Bảo đảm có tài khoản admin, đúng quyền admin và đăng nhập được bằng `password` (ADMIN_PASSWORD).

        Gọi mỗi lần khởi động: mật khẩu bị đổi ở chỗ khác sẽ được đặt lại theo ADMIN_PASSWORD, nên đổi ADMIN_PASSWORD
        rồi khởi động lại cũng là cách lấy lại quyền admin khi quên mật khẩu."""
        acc = self.get(username)
        if acc is None:
            self.create(username, "Quản trị viên (Admin)", password, role="admin", is_approved=True)
            return
        if acc.get("role") != "admin" or not acc.get("is_approved"):
            self.set_permission(username, "admin", True)
        if not verify_password(password, acc.get("password_hash") or ""):
            self.set_password(username, password)
            log.warning("Đã đặt lại mật khẩu tài khoản %s theo ADMIN_PASSWORD.", username)

    # ---- vị trí đang soát ----

    def save_progress(self, username: str, document_id: str, unit_id: str | None, page: int) -> None:
        username = (username or "").strip()
        if not username or not unit_id:
            return
        if hasattr(self.catalog, "save_progress"):
            self.catalog.save_progress(username, document_id, unit_id, page)
            return
        with self._lock:
            self._progress[(username, document_id)] = {
                "username": username, "document_id": document_id, "last_unit_id": unit_id, "last_page": page,
            }

    def get_progress(self, username: str, document_id: str) -> dict | None:
        username = (username or "").strip()
        if not username:
            return None
        if hasattr(self.catalog, "get_user_progress"):
            return self.catalog.get_user_progress(username, document_id)
        with self._lock:
            return self._progress.get((username, document_id))
