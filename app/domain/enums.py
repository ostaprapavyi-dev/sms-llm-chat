"""Enumerations shared by the domain, the storage layer and the API."""

from __future__ import annotations

from enum import StrEnum


class ConversationStatus(StrEnum):
    """Lifecycle of a single inbound message and its answer."""

    RECEIVED = "received"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class DeliveryStatus(StrEnum):
    """What the SMS provider told us about the outbound reply."""

    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    UNKNOWN = "unknown"


class Feedback(StrEnum):
    """How the user rated the generated answer."""

    NONE = "none"
    POSITIVE = "positive"
    NEGATIVE = "negative"
