"""Tài khoản, phân quyền và vị trí đang soát của từng người.

Chế độ PostgreSQL: lưu trong bảng reviewer_account / reviewer_progress (qua DbCatalog).
Chế độ thư mục file: lưu trong bộ nhớ của tiến trình (mất khi khởi động lại; chế độ này không lưu được thẩm định).
"""
from __future__ import annotations

import logging
import secrets
import threading
from pathlib import Path

from .auth import hash_password, verify_password, write_private

log = logging.getLogger("review-app")

ROLES = ("admin", "approver", "reviewer")
ROLE_LABELS = {"admin": "Admin", "approver": "Người phê duyệt", "reviewer": "Người chỉnh sửa"}
DEFAULT_ROLE = "reviewer"
INITIAL_ADMIN_PASSWORD_FILE = "initial_admin_password.txt"


def normalize_role(role: str | None) -> str:
    """Vai trò hợp lệ; vai trò cũ hoặc lạ (ví dụ 'viewer' đã bỏ) thành người chỉnh sửa."""
    return role if role in ROLES else DEFAULT_ROLE


def is_active(user: dict | None) -> bool:
    """Tài khoản đang hoạt động (Admin chưa khóa). Tài khoản bị khóa không đăng nhập được và mọi phiên của nó hết hiệu lực."""
    return bool(user) and bool(user.get("is_approved"))


def is_admin(user: dict | None) -> bool:
    return is_active(user) and user.get("role") == "admin"


def is_approver(user: dict | None) -> bool:
    return is_active(user) and user.get("role") == "approver"


def can_edit_own(user: dict | None) -> bool:
    """Người chỉnh sửa đang hoạt động: sửa/duyệt/gắn cờ trên bản riêng của mình."""
    return is_active(user) and user.get("role") == "reviewer"


def can_finalize(user: dict | None) -> bool:
    """Người phê duyệt hoặc Admin: chọn bản, duyệt bản chốt, trả lại, hoàn tất phê duyệt."""
    return is_active(user) and user.get("role") in ("approver", "admin")


def can_review(user: dict | None) -> bool:
    """Có quyền thao tác thẩm định nào đó (mọi vai trò, miễn tài khoản chưa bị khóa)."""
    return is_active(user) and user.get("role") in ROLES


def public(account: dict | None) -> dict | None:
    """Bản sao không có password_hash, an toàn để đưa vào template."""
    if account is None:
        return None
    clean = {k: v for k, v in account.items() if k != "password_hash"}
    clean["role"] = normalize_role(clean.get("role"))
    return clean


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
            acc = self.catalog.get_account(username)
        else:
            with self._lock:
                acc = self._accounts.get(username)
                acc = dict(acc) if acc else None
        if acc is not None:
            acc["role"] = normalize_role(acc.get("role"))
        return acc

    def authenticate(self, username: str, password: str) -> dict | None:
        """Tài khoản nếu đúng mật khẩu; None nếu sai (thời gian xử lý như nhau dù tài khoản có hay không)."""
        acc = self.get(username)
        if acc is None and username and username != username.lower():
            acc = self.get(username.lower())
        ok = verify_password(password, acc.get("password_hash") if acc else None)
        return acc if ok and acc else None

    def create(self, username: str, display_name: str, password: str, role: str = DEFAULT_ROLE,
               is_approved: bool = True) -> dict | None:
        """Tạo tài khoản mới (chỉ Admin làm việc này). Trả None nếu tên đã có (không ghi đè)."""
        username = username.strip()
        role = normalize_role(role)
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
        role = normalize_role(role)
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

    def ensure_admin(self, username: str, password: str | None, storage_dir: Path | None = None) -> None:
        """Bảo đảm có tài khoản admin đang hoạt động.

        - Chưa có: tạo với `password` (ADMIN_PASSWORD); không cấu hình thì sinh mật khẩu ngẫu nhiên và ghi vào
          <storage_dir>/initial_admin_password.txt (chỉ chủ tệp đọc được). Log chỉ ghi đường dẫn, không ghi mật khẩu.
        - Đã có: trả lại quyền admin và mở khóa; đặt lại mật khẩu CHỈ khi ADMIN_PASSWORD được cấu hình và khác mật khẩu
          hiện tại (cách lấy lại quyền khi quên mật khẩu). Không cấu hình thì mật khẩu admin không bao giờ bị đổi ngầm."""
        acc = self.get(username)
        if acc is None:
            generated = not password
            password = password or secrets.token_urlsafe(12)
            self.create(username, "Quản trị viên (Admin)", password, role="admin", is_approved=True)
            if generated:
                self._announce_generated_password(username, password, storage_dir)
            return
        if acc.get("role") != "admin" or not acc.get("is_approved"):
            self.set_permission(username, "admin", True)
        if password and not verify_password(password, acc.get("password_hash") or ""):
            self.set_password(username, password)
            log.warning("Đã đặt lại mật khẩu tài khoản %s theo ADMIN_PASSWORD.", username)

    @staticmethod
    def _announce_generated_password(username: str, password: str, storage_dir: Path | None) -> None:
        if storage_dir is None:
            log.warning("Đã tạo tài khoản %s với mật khẩu ngẫu nhiên nhưng không có STORAGE_DIR để ghi ra; "
                        "đặt ADMIN_PASSWORD rồi khởi động lại để đăng nhập.", username)
            return
        path = Path(storage_dir) / INITIAL_ADMIN_PASSWORD_FILE
        try:
            write_private(path, f"{username}\n{password}\n")
        except OSError as exc:
            log.warning("Đã tạo tài khoản %s nhưng không ghi được %s (%s); đặt ADMIN_PASSWORD rồi khởi động lại.",
                        username, path, exc)
            return
        log.warning("Đã tạo tài khoản %s với mật khẩu ngẫu nhiên, xem trong %s. Đăng nhập, đổi mật khẩu rồi xóa tệp này.",
                    username, path)

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
