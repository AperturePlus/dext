"""SQLAlchemy models for R7b-lite recommendation application state."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False,
    )


class AppIdentity(TimestampMixin, Base):
    __tablename__ = "app_identities"

    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppProfile(TimestampMixin, Base):
    __tablename__ = "app_profiles"

    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ConversationSession(TimestampMixin, Base):
    __tablename__ = "conversation_sessions"
    __table_args__ = (
        Index("ix_conversation_sessions_owner_root", "owner_id", "root_session_id"),
        Index("ix_conversation_sessions_owner_deleted", "owner_id", "deleted_at"),
    )

    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    root_session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_session_id: Mapped[str | None] = mapped_column(String(36))
    source_turn_id: Mapped[str | None] = mapped_column(String(36))
    professor_id: Mapped[str | None] = mapped_column(String(256))
    title: Mapped[str | None] = mapped_column(String(256))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationTurn(TimestampMixin, Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint("owner_id", "session_id", "ordinal", name="uq_turn_ordinal"),
        ForeignKeyConstraint(
            ["owner_id", "session_id"],
            ["conversation_sessions.owner_id", "conversation_sessions.id"],
            ondelete="CASCADE",
        ),
        Index("ix_conversation_turns_owner_session", "owner_id", "session_id"),
    )

    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    route: Mapped[str | None] = mapped_column(String(64))
    user_message_id: Mapped[str] = mapped_column(String(36), nullable=False)
    active_attempt_id: Mapped[str | None] = mapped_column(String(36))
    context_json: Mapped[dict | None] = mapped_column(JSON)
    snapshot_json: Mapped[dict | None] = mapped_column(JSON)
    version_pins_json: Mapped[dict | None] = mapped_column(JSON)


class ConversationAttempt(TimestampMixin, Base):
    __tablename__ = "conversation_attempts"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id", name="uq_attempt_request"),
        ForeignKeyConstraint(
            ["owner_id", "turn_id"],
            ["conversation_turns.owner_id", "conversation_turns.id"],
            ondelete="CASCADE",
        ),
        Index("ix_conversation_attempts_owner_turn", "owner_id", "turn_id"),
    )

    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(128))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationMessage(TimestampMixin, Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "turn_id"],
            ["conversation_turns.owner_id", "conversation_turns.id"],
            ondelete="CASCADE",
        ),
        Index("ix_conversation_messages_owner_turn", "owner_id", "turn_id"),
        Index("ix_conversation_messages_owner_session", "owner_id", "session_id"),
    )

    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(String(36))
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    related_recommendations_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    feedback: Mapped[str] = mapped_column(String(32), nullable=False, default="none")


class ConversationSummaryModel(TimestampMixin, Base):
    __tablename__ = "conversation_summaries"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "session_id", "through_turn_id",
            name="uq_conversation_summary_turn",
        ),
    )

    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    through_turn_id: Mapped[str | None] = mapped_column(String(36))
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")


class AppFavorite(TimestampMixin, Base):
    __tablename__ = "app_favorites"

    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    professor_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    favorited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AppHistory(TimestampMixin, Base):
    __tablename__ = "app_history"

    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), primary_key=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version_pins_json: Mapped[dict | None] = mapped_column(JSON)


class IdempotencyRecord(TimestampMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("owner_id", "scope", "key", name="uq_idempotency_scope_key"),
        Index("ix_idempotency_expires", "expires_at"),
    )

    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(256), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(256))
    response_json: Mapped[dict | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


REQUIRED_TABLES = tuple(Base.metadata.tables)


__all__ = [
    "AppFavorite",
    "AppHistory",
    "AppIdentity",
    "AppProfile",
    "Base",
    "ConversationAttempt",
    "ConversationMessage",
    "ConversationSession",
    "ConversationSummaryModel",
    "ConversationTurn",
    "IdempotencyRecord",
    "REQUIRED_TABLES",
    "utcnow",
]
