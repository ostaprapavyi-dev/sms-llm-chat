"""SQLAlchemy mapping for the conversations table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domain.enums import ConversationStatus, DeliveryStatus, Feedback
from app.domain.models import new_conversation_id, utcnow


def _enum(python_enum: type, name: str) -> Enum:
    """Store enum *values* (not member names) as a portable VARCHAR + CHECK."""
    return Enum(
        python_enum,
        name=name,
        native_enum=False,
        length=20,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


class Base(DeclarativeBase):
    pass


class ConversationRow(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_conversation_id)
    phone_number: Mapped[str] = mapped_column(String(20), index=True)
    incoming_message: Mapped[str] = mapped_column(Text)
    llm_response: Mapped[str | None] = mapped_column(Text, default=None)

    # Unique: the provider retries webhooks, and a retry must not create a second
    # conversation or trigger a second LLM call.
    provider_message_id: Mapped[str] = mapped_column(String(64), unique=True)
    provider_response_id: Mapped[str | None] = mapped_column(String(64), default=None)

    status: Mapped[ConversationStatus] = mapped_column(
        _enum(ConversationStatus, "conversation_status"),
        default=ConversationStatus.RECEIVED,
    )
    delivery_status: Mapped[DeliveryStatus | None] = mapped_column(
        _enum(DeliveryStatus, "delivery_status"), default=None
    )
    feedback: Mapped[Feedback] = mapped_column(
        _enum(Feedback, "feedback"), default=Feedback.NONE
    )
    # Which inbound SMS produced the rating: an audit trail, and the guard that keeps a
    # retried feedback webhook from being applied (and acknowledged) twice.
    feedback_message_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, default=None
    )
    error_message: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    # Serves both admin lookups and "latest conversation for this phone number".
    __table_args__ = (Index("ix_conversations_phone_created", "phone_number", "created_at"),)
