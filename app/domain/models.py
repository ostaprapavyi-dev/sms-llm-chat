"""Domain models.

These are provider- and storage-agnostic: an SMS provider turns its own payload into an
:class:`InboundMessage`, and the repository returns :class:`Conversation` objects no
matter which database sits underneath.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.schemas import CamelModel
from app.domain.enums import ConversationStatus, DeliveryStatus, Feedback


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_conversation_id() -> str:
    return f"conv_{uuid.uuid4().hex[:12]}"


class InboundMessage(BaseModel):
    """A normalised incoming SMS, whatever provider it arrived from."""

    phone_number: str
    body: str
    provider_message_id: str
    timestamp: datetime | None = None
    provider: str = "unknown"


class Conversation(BaseModel):
    """One inbound message together with the answer we produced for it."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(default_factory=new_conversation_id)
    phone_number: str
    incoming_message: str
    llm_response: str | None = None
    provider_message_id: str
    provider_response_id: str | None = None
    status: ConversationStatus = ConversationStatus.RECEIVED
    delivery_status: DeliveryStatus | None = None
    feedback: Feedback = Feedback.NONE
    feedback_message_id: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ConversationUpdate(BaseModel):
    """Partial update; only the fields explicitly set are written."""

    llm_response: str | None = None
    provider_response_id: str | None = None
    status: ConversationStatus | None = None
    delivery_status: DeliveryStatus | None = None
    feedback: Feedback | None = None
    feedback_message_id: str | None = None
    error_message: str | None = None

    def changes(self) -> dict[str, object]:
        return self.model_dump(exclude_unset=True)


class ConversationResponse(CamelModel):
    """API representation -- camelCase, as in the assignment example."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    phone_number: str
    incoming_message: str
    llm_response: str | None
    provider_message_id: str
    provider_response_id: str | None
    status: ConversationStatus
    delivery_status: DeliveryStatus | None
    feedback: Feedback
    created_at: datetime
    updated_at: datetime
