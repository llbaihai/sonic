import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Integer, Index, func, BigInteger, CheckConstraint, Boolean
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class Record:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Record, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))


class Project(Record, Base):
    __tablename__ = "projects"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    __table_args__ = (Index("ix_projects_owner_id_id", "owner_id", "id"),)


class Idempotency(Base):
    __tablename__ = "idempotency"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    scope: Mapped[str] = mapped_column(String(160), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RateLimit(Base):
    __tablename__ = "rate_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window: Mapped[int] = mapped_column(Integer, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


class Asset(Record, Base):
    __tablename__ = "assets"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(String(160))
    media_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    codec: Mapped[str] = mapped_column(String(32))
    duration_ms: Mapped[int] = mapped_column(Integer)
    sample_rate: Mapped[int] = mapped_column(Integer)
    channels: Mapped[int] = mapped_column(Integer)
    __table_args__ = (CheckConstraint('size_bytes > 0 AND duration_ms > 0', name='ck_asset_positive'),)


class FileDeletion(Base):
    __tablename__ = "file_deletions"
    storage_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Draft(Base):
    __tablename__ = "drafts"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    tracks: Mapped[list] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (CheckConstraint('version > 0', name='ck_draft_version'),)


class Work(Record, Base):
    __tablename__ = "works"
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('assets.id', ondelete='CASCADE'), index=True)
    title: Mapped[str] = mapped_column(String(120))


class LoginSession(Record, Base):
    __tablename__ = "login_sessions"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
