from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import AuthMethod, StaffRole, SubjectType
from app.models.base import Base, pg_enum


class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tg_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    tg_username: Mapped[str | None] = mapped_column(Text)
    role: Mapped[StaffRole] = mapped_column(pg_enum(StaffRole, "staff_role"), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    is_superadmin: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    login: Mapped[str | None] = mapped_column(Text, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    totp_secret: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    invited_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    invite_expires_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    last_login_at: Mapped[datetime | None]


class AuthSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_subject", "subject_type", "subject_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    subject_type: Mapped[SubjectType] = mapped_column(pg_enum(SubjectType, "subject_type"), nullable=False)
    subject_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    auth_method: Mapped[AuthMethod] = mapped_column(pg_enum(AuthMethod, "auth_method"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None]
    ip: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)


class LoginAttempt(Base):
    """Failed password login attempts (brute-force protection survives restarts)."""

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_created", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    login: Mapped[str] = mapped_column(Text, nullable=False)
    ip: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
