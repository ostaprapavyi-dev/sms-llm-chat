"""Twilio SMS provider.

Covers both reply strategies Twilio supports: sending through the REST API (default) and
answering the webhook synchronously with Messaging Response XML (``SMS_REPLY_MODE=twiml``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from twilio.base.exceptions import TwilioException
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse

from app.core.errors import InvalidWebhookError, SignatureVerificationError, SmsProviderError
from app.core.logging import truncate
from app.core.phone import normalize_phone
from app.domain.enums import DeliveryStatus
from app.domain.models import InboundMessage
from app.providers.sms.base import SendResult

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Twilio-Signature"

# Twilio's message resource statuses mapped onto our own vocabulary.
_STATUS_MAP = {
    "queued": DeliveryStatus.QUEUED,
    "accepted": DeliveryStatus.QUEUED,
    "scheduled": DeliveryStatus.QUEUED,
    "sending": DeliveryStatus.QUEUED,
    "sent": DeliveryStatus.SENT,
    "delivered": DeliveryStatus.DELIVERED,
    "undelivered": DeliveryStatus.FAILED,
    "failed": DeliveryStatus.FAILED,
}


def build_twiml(message: str) -> str:
    """Render ``<Response><Message>...</Message></Response>`` with proper escaping."""
    response = MessagingResponse()
    response.message(message)
    return str(response)


class TwilioProvider:
    """Sends via the Twilio REST API and parses Twilio's form-encoded webhooks."""

    name = "twilio"

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        validate_signature: bool = True,
        client: Client | None = None,
    ) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        # Without an auth token there is nothing to validate against; this is what makes
        # the parser usable locally, before any Twilio credentials exist.
        self.validate_signature = validate_signature and bool(auth_token)
        self._client = client
        self._validator = RequestValidator(auth_token) if auth_token else None

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = Client(self.account_sid, self.auth_token)
        return self._client

    async def send(self, to: str, body: str) -> SendResult:
        try:
            # The Twilio SDK is synchronous; keep it off the event loop.
            message = await asyncio.to_thread(
                self.client.messages.create, to=to, from_=self.from_number, body=body
            )
        except TwilioException as exc:
            logger.warning("twilio send failed", extra={"to": to, "error": str(exc)})
            raise SmsProviderError(f"Twilio could not send the message: {exc}") from exc

        status = _STATUS_MAP.get(str(getattr(message, "status", "")), DeliveryStatus.UNKNOWN)
        logger.info(
            "twilio sms sent",
            extra={"to": to, "messageId": message.sid, "status": status, "body": truncate(body)},
        )
        return SendResult(message_id=message.sid, status=status)

    def parse_webhook(self, payload: Mapping[str, Any]) -> InboundMessage:
        body = payload.get("Body")
        message_sid = payload.get("MessageSid") or payload.get("SmsMessageSid")
        if body is None:
            raise InvalidWebhookError("Twilio payload is missing 'Body'")
        if not message_sid:
            raise InvalidWebhookError("Twilio payload is missing 'MessageSid'")

        return InboundMessage(
            phone_number=normalize_phone(payload.get("From")),
            body=str(body),
            provider_message_id=str(message_sid),
            # Twilio does not send an origin timestamp on the inbound webhook.
            timestamp=None,
            provider=self.name,
        )

    def verify_signature(
        self, *, url: str, headers: Mapping[str, str], payload: Mapping[str, Any]
    ) -> None:
        if not self.validate_signature:
            logger.debug("twilio signature validation disabled")
            return

        signature = headers.get(SIGNATURE_HEADER) or headers.get(SIGNATURE_HEADER.lower())
        if not signature:
            raise SignatureVerificationError("Missing X-Twilio-Signature header")

        assert self._validator is not None  # guarded by validate_signature
        if not self._validator.validate(url, dict(payload), signature):
            raise SignatureVerificationError("Twilio signature does not match the request")
