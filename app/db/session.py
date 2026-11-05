"""Khởi tạo kết nối DB, session và seed dữ liệu ban đầu."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, User, UserRole

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "review.db"


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        # Hỗ trợ postgres:// chuyển thành postgresql:// cho SQLAlchemy 2.0
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url
    DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_DB_PATH}"


engine = create_engine(
    get_database_url(),
    connect_args={"check_same_thread": False} if get_database_url().startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db(target_engine=None):
    """Tạo bảng và seed các tài khoản reviewer mẫu nếu chưa có."""
    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)

    with Session(eng) as session:
        # Seed users nếu bảng đang trống
        count = session.query(User).count()
        if count == 0:
            seed_users = [
                User(username="lead", full_name="Trưởng nhóm Thẩm định", role=UserRole.LEAD),
                User(username="duy.nguyen", full_name="Nguyễn Đức Duy (Reviewer)", role=UserRole.REVIEWER),
                User(username="dung.nguyen", full_name="Nguyễn Đăng Hùng Dũng (Reviewer)", role=UserRole.REVIEWER),
                User(username="phuong.tran", full_name="Trần Thị Mai Phương (Reviewer)", role=UserRole.REVIEWER),
                User(username="long.luu", full_name="Lưu Thiên Long (Reviewer)", role=UserRole.REVIEWER),
            ]
            session.add_all(seed_users)
            session.commit()


def get_db() -> Generator[Session, None, None]:
    """Dependency cung cấp session cho FastAPI endpoints."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
