"""Inbound SMS webhooks.

Two routes, one flow: a provider-neutral JSON endpoint (the shape in the assignment)
and a Twilio form-encoded endpoint. Both normalise the payload into an
:class:`~app.domain.models.InboundMessage` and hand it to the conversation service.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request, Response, status
from pydantic import BaseModel
from starlette.formparsers import MultiPartException

from app.api.deps import ConversationServiceDep, SettingsDep, TwilioParserDep
from app.core.errors import InvalidWebhookError
from app.domain.models import InboundMessage
from app.providers.sms.base import parse_generic_payload
from app.providers.sms.twilio import build_twiml
from app.services.conversation_service import ConversationService, MessageKind

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


class WebhookAck(BaseModel):
    """Response of the JSON webhook: the carrier only needs to know we took it.

    ``conversation_id`` is null for feedback that had no conversation to attach to.
    """

    status: str
    conversation_id: str | None = None
    reply: str | None = None


async def _accept_and_schedule(
    message: InboundMessage,
    service: ConversationService,
    background: BackgroundTasks,
) -> WebhookAck:
    """Store the message, answer the carrier now, do the slow work afterwards."""
    accepted = await service.accept(message)

    if accepted.kind is MessageKind.DUPLICATE:
        return WebhookAck(status="duplicate", conversation_id=accepted.conversation.id)

    if accepted.kind is MessageKind.FEEDBACK:
        reply = accepted.reply or ""
        background.add_task(service.send_reply_in_background, message.phone_number, reply)
        return WebhookAck(
            status="feedback",
            conversation_id=accepted.conversation.id if accepted.conversation else None,
            reply=reply,
        )

    background.add_task(service.process_in_background, accepted.conversation)
    return WebhookAck(status="accepted", conversation_id=accepted.conversation.id)


@router.post(
    "/sms",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WebhookAck,
    summary="Receive an SMS from any provider using the generic JSON shape",
)
async def receive_sms(
    payload: dict[str, Any],
    background: BackgroundTasks,
    service: ConversationServiceDep,
    settings: SettingsDep,
) -> WebhookAck:
    message = parse_generic_payload(payload)

    if settings.sms_reply_mode == "twiml":
        # Synchronous mode: the answer is part of this response.
        result = await service.handle_inbound(message, deliver=False)
        return WebhookAck(
            status="completed" if result.kind is MessageKind.QUESTION else result.kind.value,
            conversation_id=result.conversation.id if result.conversation else None,
            reply=result.reply,
        )

    return await _accept_and_schedule(message, service, background)


@router.post(
    "/sms/twilio",
    summary="Receive an SMS from Twilio (form-encoded, signature-verified)",
    response_class=Response,
)
async def receive_twilio_sms(
    request: Request,
    background: BackgroundTasks,
    service: ConversationServiceDep,
    settings: SettingsDep,
    twilio: TwilioParserDep,
) -> Response:
    try:
        form = dict(await request.form())
    except (MultiPartException, ValueError, UnicodeDecodeError) as exc:
        raise InvalidWebhookError("Could not read the Twilio form payload") from exc

    twilio.verify_signature(url=str(request.url), headers=request.headers, payload=form)
    message = twilio.parse_webhook(form)

    if settings.sms_reply_mode == "twiml":
        result = await service.handle_inbound(message, deliver=False)
        return Response(content=build_twiml(result.reply), media_type="application/xml")

    ack = await _accept_and_schedule(message, service, background)
    logger.info("twilio webhook accepted", extra={"conversationId": ack.conversation_id})
    # Twilio expects TwiML; an empty response means "no immediate reply", the answer
    # follows over the REST API.
    return Response(content=EMPTY_TWIML, media_type="application/xml")
