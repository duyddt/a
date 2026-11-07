"""Xác thực người dùng đơn giản: hỗ trợ chọn tài khoản hoặc đăng nhập nhanh."""
from __future__ import annotations

from fastapi import Request, Response
from sqlalchemy.orm import Session

from app.db.models import User, UserRole

DEFAULT_USERNAME = "duy.nguyen"
COOKIE_NAME = "reviewer_username"


def get_all_users(db: Session) -> list[User]:
    """Danh sách tất cả người dùng trong hệ thống."""
    return db.query(User).order_by(User.id.asc()).all()


def get_current_user(db: Session, request: Request) -> User:
    """Lấy user hiện tại từ cookie, hoặc trả về user mặc định nếu chưa chọn."""
    username = request.cookies.get(COOKIE_NAME)
    if username:
        user = db.query(User).filter(User.username == username).first()
        if user:
            return user

    # Fallback user mặc định
    default_user = db.query(User).filter(User.username == DEFAULT_USERNAME).first()
    if not default_user:
        # Nếu chưa có thì lấy user đầu tiên
        default_user = db.query(User).first()
    return default_user


def set_user_cookie(response: Response, username: str) -> None:
    """Ghi cookie chọn người dùng."""
    response.set_cookie(key=COOKIE_NAME, value=username, max_age=86400 * 30, httponly=True, samesite="lax")
