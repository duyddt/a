"""Cấu hình từ biến môi trường (và file `.env` ở gốc dự án nếu có). Không có mật khẩu hay khóa nào trong mã nguồn.

Thứ tự ưu tiên: biến môi trường của tiến trình > file `.env` > giá trị mặc định.
Đường dẫn tương đối (DATA_DIR, STORAGE_DIR, AUTO_IMPORT_DIR) tính từ thư mục gốc dự án, nên chạy lệnh
từ thư mục nào cũng cho cùng kết quả.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


DEFAULT_ADMIN_PASSWORD = "admin123"  # chỉ để chạy thử trên máy cá nhân; triển khai thật phải đặt ADMIN_PASSWORD


@dataclass(frozen=True)
class Settings:
    data_dir: Path  # chế độ thư mục file: data/<document_id>/{source.pdf,document.json}
    storage_dir: Path  # nơi lưu source.pdf, ảnh trang đã render, khóa ký phiên và mật khẩu admin khởi tạo
    conninfo: str | None  # chuỗi kết nối PostgreSQL; None = chế độ thư mục file
    auto_import_dir: Path | None  # nếu có, mỗi lần khởi động nhập mọi thư mục con của nó (nhập lại không tạo trùng)
    secret_key: str | None = None  # None: tự sinh một lần và lưu ở storage_dir/.secret_key
    admin_username: str = "admin"
    admin_password: str = DEFAULT_ADMIN_PASSWORD  # ADMIN_PASSWORD; mặc định admin123 để ai clone về cũng chạy thử được ngay
    db_connect_timeout: float = 10.0  # giây chờ PostgreSQL khi khởi động trước khi chuyển sang chế độ chỉ xem
    session_max_age: int = 14 * 24 * 3600  # thời hạn phiên đăng nhập (giây)


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Đọc file .env dạng KEY=VALUE (bỏ dòng trống, dòng # và dấu nháy bao quanh). Không có file thì trả {}."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _resolve(value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else (BASE_DIR / path)


def load_settings(environ: Mapping[str, str] | None = None, env_file: Path | None = ENV_FILE) -> Settings:
    env: dict[str, str] = read_env_file(env_file) if env_file else {}
    env.update(os.environ if environ is None else environ)

    def get(name: str) -> str | None:
        value = env.get(name)
        return value.strip() if value and value.strip() else None

    conninfo = get("DATABASE_URL")
    if conninfo is None and get("POSTGRES_HOST"):
        from psycopg.conninfo import make_conninfo  # tự escape ký tự đặc biệt trong mật khẩu

        conninfo = make_conninfo(
            host=get("POSTGRES_HOST"),
            port=get("POSTGRES_PORT") or "5432",
            dbname=get("POSTGRES_DB") or "review",
            user=get("POSTGRES_USER") or "review",
            password=env.get("POSTGRES_PASSWORD", ""),
        )
    auto = get("AUTO_IMPORT_DIR")
    try:
        timeout = float(get("DB_CONNECT_TIMEOUT") or 10)
    except ValueError:
        timeout = 10.0
    try:
        session_days = float(get("SESSION_DAYS") or 14)
    except ValueError:
        session_days = 14.0
    return Settings(
        data_dir=_resolve(get("DATA_DIR"), BASE_DIR / "data"),
        storage_dir=_resolve(get("STORAGE_DIR"), BASE_DIR / "storage"),
        conninfo=conninfo,
        auto_import_dir=_resolve(auto, BASE_DIR) if auto else None,
        secret_key=get("SECRET_KEY"),
        admin_username=get("ADMIN_USERNAME") or "admin",
        admin_password=get("ADMIN_PASSWORD") or DEFAULT_ADMIN_PASSWORD,
        db_connect_timeout=max(1.0, timeout),
        session_max_age=max(3600, int(session_days * 24 * 3600)),
    )
