"""The contract every SMS provider implements.

A provider owns both directions of the wire: it parses the webhook the carrier posts to
us and it sends the reply back. Everything above this layer speaks
:class:`~app.domain.models.InboundMessage` and :class:`SendResult` only.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from app.core.errors import InvalidWebhookError
from app.core.phone import normalize_phone
from app.domain.enums import DeliveryStatus
from app.domain.models import InboundMessage


class SendResult(BaseModel):
    """What the provider reported about an outbound message."""

    message_id: str | None = None
    status: DeliveryStatus = DeliveryStatus.UNKNOWN


@runtime_checkable
class SmsProvider(Protocol):
    name: str

    async def send(self, to: str, body: str) -> SendResult:
        """Deliver ``body`` to ``to``.

        Must raise :class:`~app.core.errors.SmsProviderError` on failure.
        """
        ...

    def parse_webhook(self, payload: Mapping[str, Any]) -> InboundMessage:
        """Turn the provider's webhook payload into a normalised inbound message."""
        ...

    def verify_signature(
        self, *, url: str, headers: Mapping[str, str], payload: Mapping[str, Any]
    ) -> None:
        """Raise :class:`~app.core.errors.SignatureVerificationError` if the request is
        not authentic. Providers without signatures do nothing."""
        ...


def _first(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_generic_payload(
    payload: Mapping[str, Any], *, provider: str = "generic"
) -> InboundMessage:
    """Parse the provider-neutral JSON shape described in the assignment.

    ``{"from": ..., "body": ..., "messageId": ..., "timestamp": ...}`` -- snake_case
    variants are accepted too, so a curl example does not have to be exact.
    """
    sender = _first(payload, "from", "From", "sender", "phoneNumber", "phone_number")
    body = _first(payload, "body", "Body", "message", "text")
    message_id = _first(payload, "messageId", "message_id", "MessageId", "id")

    if body is None:
        raise InvalidWebhookError("Field 'body' is required")
    if message_id is None:
        raise InvalidWebhookError("Field 'messageId' is required")

    raw_timestamp = _first(payload, "timestamp", "Timestamp", "createdAt")
    timestamp: datetime | None = None
    if raw_timestamp is not None:
        if isinstance(raw_timestamp, datetime):
            timestamp = raw_timestamp
        else:
            try:
                timestamp = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
            except ValueError as exc:
                raise InvalidWebhookError(f"Invalid timestamp '{raw_timestamp}'") from exc

    return InboundMessage(
        phone_number=normalize_phone(str(sender) if sender is not None else None),
        body=str(body),
        provider_message_id=str(message_id),
        timestamp=timestamp,
        provider=provider,
    )
