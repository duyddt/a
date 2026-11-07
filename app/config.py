"""Cấu hình từ biến môi trường. Không có mật khẩu hay khóa nào trong mã nguồn."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path  # chế độ thư mục file: data/<document_id>/{source.pdf,document.json}
    storage_dir: Path  # chế độ PostgreSQL: nơi lưu source.pdf và ảnh trang đã render
    conninfo: str | None  # chuỗi kết nối PostgreSQL; None = chế độ thư mục file
    auto_import_dir: Path | None  # nếu có, mỗi lần khởi động nhập mọi thư mục con của nó (nhập lại không tạo trùng)


def load_settings() -> Settings:
    try:
        from dotenv import load_dotenv

        load_dotenv(BASE_DIR / ".env")
    except Exception:
        pass

    conninfo = os.environ.get("DATABASE_URL") or None
    if conninfo is None and os.environ.get("POSTGRES_HOST"):
        from psycopg.conninfo import make_conninfo  # tự escape ký tự đặc biệt trong mật khẩu

        conninfo = make_conninfo(
            host=os.environ["POSTGRES_HOST"],
            port=os.environ.get("POSTGRES_PORT", "5432"),
            dbname=os.environ.get("POSTGRES_DB", "review"),
            user=os.environ.get("POSTGRES_USER", "review"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
        )
    if conninfo is None:
        # Tự động kết nối PostgreSQL nội bộ nếu có sẵn dịch vụ trên máy
        default_url = "postgresql://review@localhost:5432/review"
        try:
            import psycopg

            with psycopg.connect(default_url, connect_timeout=1):
                conninfo = default_url
        except Exception:
            pass

    auto = os.environ.get("AUTO_IMPORT_DIR")
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR") or BASE_DIR / "data"),
        storage_dir=Path(os.environ.get("STORAGE_DIR") or BASE_DIR / "storage"),
        conninfo=conninfo,
        auto_import_dir=Path(auto) if auto else None,
    )
