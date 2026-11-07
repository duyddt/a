"""SQLAlchemy models cho việc lưu kết quả thẩm định và bản sửa."""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UserRole(str, enum.Enum):
    LEAD = "lead"
    REVIEWER = "reviewer"


class DecisionType(str, enum.Enum):
    APPROVED = "approved"
    FLAGGED = "flagged"
    STRUCTURAL_ERROR = "structural_error"


class StructuralErrorType(str, enum.Enum):
    WRONG_LEVEL = "wrong_level"          # Sai cấp (Điều/Khoản/Điểm)
    WRONG_NUMBER = "wrong_number"        # Sai số thứ tự
    NEED_SPLIT = "need_split"            # Cần tách đơn vị
    NEED_MERGE = "need_merge"            # Cần gộp đơn vị


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.REVIEWER, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    decisions: Mapped[list["ReviewDecision"]] = relationship("ReviewDecision", back_populates="user")
    revisions: Mapped[list["UnitRevision"]] = relationship("UnitRevision", back_populates="user")


class ReviewDecision(Base):
    """Bảng quyết định thẩm định: đơn vị, người, quyết định, ghi chú, thời điểm."""
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    unit_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    decision: Mapped[DecisionType] = mapped_column(Enum(DecisionType), nullable=False)
    structural_error_type: Mapped[Optional[StructuralErrorType]] = mapped_column(
        Enum(StructuralErrorType), nullable=True
    )
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="decisions")

    __table_args__ = (
        Index("idx_review_decisions_doc_unit", "document_id", "unit_id", "created_at"),
    )


class UnitRevision(Base):
    """Bảng bản sửa chữ: đơn vị, nội dung trước, nội dung sau, người, thời điểm.
    NGHIÊM CẤM UPDATE HOẶC DELETE (APPEND-ONLY).
    """
    __tablename__ = "unit_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    unit_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    content_before: Mapped[str] = mapped_column(Text, nullable=False)
    content_after: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="revisions")

    __table_args__ = (
        Index("idx_unit_revisions_doc_unit", "document_id", "unit_id", "created_at"),
    )
