"""In-memory SMS provider.

Lets the whole flow -- webhook in, LLM, reply out -- run end to end with no carrier
account and no network. Sent messages land in :attr:`MockSmsProvider.outbox`, which the
tests assert on and ``GET /debug/outbox`` exposes locally.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from app.core.logging import truncate
from app.domain.enums import DeliveryStatus
from app.domain.models import InboundMessage, utcnow
from app.providers.sms.base import SendResult, parse_generic_payload

logger = logging.getLogger(__name__)


class SentMessage(BaseModel):
    """One entry of the mock outbox."""

    to: str
    body: str
    message_id: str
    sent_at: object = None


class MockSmsProvider:
    """Records outbound messages instead of delivering them."""

    name = "mock"

    def __init__(self) -> None:
        self.outbox: list[SentMessage] = []
        self._ids = itertools.count(1)

    async def send(self, to: str, body: str) -> SendResult:
        message_id = f"MOCK{next(self._ids):08d}"
        self.outbox.append(
            SentMessage(to=to, body=body, message_id=message_id, sent_at=utcnow())
        )
        logger.info(
            "mock sms sent",
            extra={"to": to, "messageId": message_id, "body": truncate(body)},
        )
        return SendResult(message_id=message_id, status=DeliveryStatus.SENT)

    def parse_webhook(self, payload: Mapping[str, Any]) -> InboundMessage:
        return parse_generic_payload(payload, provider=self.name)

    def verify_signature(
        self, *, url: str, headers: Mapping[str, str], payload: Mapping[str, Any]
    ) -> None:
        """No signatures to verify -- the mock provider trusts its caller."""

    def clear(self) -> None:
        self.outbox.clear()
