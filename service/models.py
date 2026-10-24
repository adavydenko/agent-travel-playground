"""Typed ORM mappings for the existing SQLite schema."""

from typing import Any

from sqlalchemy import JSON, Boolean, Index, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Access(Base):
    """An operator-issued access credential; only its hash is stored."""

    __tablename__ = 'students'

    student: Mapped[str] = mapped_column(Text, primary_key=True)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    expires: Mapped[float]
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default='1')
    event_id: Mapped[str | None] = mapped_column(Text)


class Attempt(Base):
    """The current attempt. Resetting it leaves audit events intact."""

    __tablename__ = 'tasks'
    __table_args__ = (UniqueConstraint('student', 'kind'),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    student: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    expires: Mapped[float]
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    receipt: Mapped[str | None] = mapped_column(Text)


class AuditEvent(Base):
    """Immutable operation evidence, including records from retired attempts."""

    __tablename__ = 'events'
    __table_args__ = (
        Index('events_task_student', 'task', 'student'),
        Index('events_student_seq', 'student', 'seq'),
        {'sqlite_autoincrement': True},
    )

    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    at: Mapped[float]
    student: Mapped[str] = mapped_column(Text)
    task: Mapped[str] = mapped_column(Text)
    tool: Mapped[str] = mapped_column(Text)
    # Preserve the exact public journal format, including legacy JSON strings.
    args: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
