"""Orchestration of the inbound-SMS flow.

This is the only place that knows the order of operations: store, generate, store,
send. It talks to the repository and the two provider protocols, never to Twilio,
Groq or SQLAlchemy directly -- which is what lets any of them be swapped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from app.config import Settings
from app.core.errors import AppError, DuplicateMessageError, SmsProviderError
from app.core.logging import truncate
from app.db.repository import ConversationRepository
from app.domain.enums import ConversationStatus, DeliveryStatus, Feedback
from app.domain.models import Conversation, ConversationUpdate, InboundMessage
from app.providers.llm.base import ChatMessage, LLMProvider
from app.providers.sms.base import SmsProvider
from app.services.feedback import classify_feedback

logger = logging.getLogger(__name__)


class MessageKind(StrEnum):
    """What an inbound message turned out to be."""

    QUESTION = "question"
    FEEDBACK = "feedback"
    DUPLICATE = "duplicate"


@dataclass
class AcceptedMessage:
    """Result of taking an inbound message in, before any answer is generated."""

    kind: MessageKind
    conversation: Conversation | None = None
    reply: str | None = None

    @property
    def needs_processing(self) -> bool:
        """Only a fresh question costs an LLM call."""
        return self.kind is MessageKind.QUESTION and self.conversation is not None


@dataclass
class HandledMessage:
    """Outcome of processing one inbound message."""

    conversation: Conversation | None
    reply: str
    kind: MessageKind = MessageKind.QUESTION

    @property
    def duplicate(self) -> bool:
        return self.kind is MessageKind.DUPLICATE


class ConversationService:
    def __init__(
        self,
        *,
        repository: ConversationRepository,
        llm: LLMProvider,
        sms: SmsProvider,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._llm = llm
        self._sms = sms
        self._settings = settings

    # -- entry points ----------------------------------------------------

    async def accept(self, message: InboundMessage) -> AcceptedMessage:
        """Take the message in and decide what it is.

        Kept separate from :meth:`process` so the webhook can store the message and
        answer the carrier immediately, then generate the reply in the background.
        """
        existing = await self._repository.get_by_provider_message_id(message.provider_message_id)
        if existing is not None:
            logger.info(
                "duplicate webhook ignored",
                extra={
                    "conversationId": existing.id,
                    "providerMessageId": message.provider_message_id,
                },
            )
            return AcceptedMessage(kind=MessageKind.DUPLICATE, conversation=existing)

        rating = classify_feedback(message.body)
        if rating is not None:
            return await self._record_feedback(message, rating)

        conversation = Conversation(
            phone_number=message.phone_number,
            incoming_message=message.body,
            provider_message_id=message.provider_message_id,
            status=ConversationStatus.RECEIVED,
        )
        try:
            conversation = await self._repository.create(conversation)
        except DuplicateMessageError:
            # Two webhook deliveries raced; the stored one wins.
            stored = await self._repository.get_by_provider_message_id(
                message.provider_message_id
            )
            if stored is None:  # pragma: no cover - only on a storage inconsistency
                raise
            return AcceptedMessage(kind=MessageKind.DUPLICATE, conversation=stored)

        logger.info(
            "inbound message stored",
            extra={
                "conversationId": conversation.id,
                "provider": message.provider,
                "body": truncate(message.body),
            },
        )
        return AcceptedMessage(kind=MessageKind.QUESTION, conversation=conversation)

    async def process(self, conversation: Conversation, *, deliver: bool = True) -> HandledMessage:
        """Generate the answer, store it, and send it back unless ``deliver`` is off.

        ``deliver=False`` is the TwiML path: the reply travels back in the webhook
        response instead of a separate API call.
        """
        conversation = await self._repository.update(
            conversation.id, ConversationUpdate(status=ConversationStatus.PROCESSING)
        )

        try:
            reply = await self._generate_reply(conversation)
        # LLMError is the expected failure; the bare Exception catches anything a
        # provider leaks, so one bad reply never takes the request down.
        except Exception as exc:
            logger.error(
                "llm generation failed",
                extra={"conversationId": conversation.id, "error": str(exc)},
            )
            conversation = await self._repository.update(
                conversation.id,
                ConversationUpdate(
                    status=ConversationStatus.FAILED, error_message=str(exc)[:500]
                ),
            )
            # The user still gets an answer -- a silent drop is the worst outcome here.
            reply = self._settings.fallback_reply
        else:
            conversation = await self._repository.update(
                conversation.id,
                ConversationUpdate(
                    llm_response=reply, status=ConversationStatus.COMPLETED, error_message=None
                ),
            )

        if deliver:
            conversation = await self._deliver(conversation, reply)
        else:
            conversation = await self._repository.update(
                conversation.id, ConversationUpdate(delivery_status=DeliveryStatus.QUEUED)
            )

        return HandledMessage(conversation=conversation, reply=reply)

    async def handle_inbound(
        self, message: InboundMessage, *, deliver: bool = True
    ) -> HandledMessage:
        """Store and process in one go (TwiML mode and tests)."""
        accepted = await self.accept(message)

        if accepted.kind is MessageKind.DUPLICATE:
            conversation = accepted.conversation
            reply = (conversation.llm_response if conversation else None) or (
                self._settings.fallback_reply
            )
            return HandledMessage(
                conversation=conversation, reply=reply, kind=MessageKind.DUPLICATE
            )

        if accepted.kind is MessageKind.FEEDBACK:
            reply = accepted.reply or self._settings.feedback_ack_reply
            if deliver:
                await self.send_reply(message.phone_number, reply)
            return HandledMessage(
                conversation=accepted.conversation, reply=reply, kind=MessageKind.FEEDBACK
            )

        assert accepted.conversation is not None  # guaranteed for QUESTION
        return await self.process(accepted.conversation, deliver=deliver)

    async def send_reply(self, to: str, text: str) -> None:
        """Send a message that is not tied to a stored answer (a feedback ack)."""
        try:
            await self._sms.send(to, text)
        except SmsProviderError as exc:
            logger.error("ack delivery failed", extra={"to": to, "error": str(exc)})

    async def send_reply_in_background(self, to: str, text: str) -> None:
        """Background-task wrapper around :meth:`send_reply`."""
        try:
            await self.send_reply(to, text)
        except Exception:  # pragma: no cover - defensive
            logger.exception("background ack crashed", extra={"to": to})

    async def process_in_background(self, conversation: Conversation) -> None:
        """Background-task wrapper: never let an exception escape unlogged."""
        try:
            await self.process(conversation)
        except AppError as exc:
            logger.error(
                "background processing failed",
                extra={"conversationId": conversation.id, "code": exc.code, "error": exc.message},
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "background processing crashed", extra={"conversationId": conversation.id}
            )

    # -- internals -------------------------------------------------------

    async def _record_feedback(self, message: InboundMessage, rating: Feedback) -> AcceptedMessage:
        """Attach a rating to the most recent conversation of that phone number.

        A rating is not a question: it is never stored as its own conversation and it
        never reaches the LLM.
        """
        latest = await self._repository.get_latest_for_phone(message.phone_number)
        if latest is None:
            logger.info(
                "feedback without a conversation to attach it to",
                extra={"phoneNumber": message.phone_number, "feedback": rating},
            )
            return AcceptedMessage(
                kind=MessageKind.FEEDBACK,
                reply=self._settings.feedback_no_conversation_reply,
            )

        conversation = await self._repository.update(
            latest.id, ConversationUpdate(feedback=rating)
        )
        logger.info(
            "feedback recorded",
            extra={"conversationId": conversation.id, "feedback": rating},
        )
        return AcceptedMessage(
            kind=MessageKind.FEEDBACK,
            conversation=conversation,
            reply=self._settings.feedback_ack_reply,
        )

    async def _generate_reply(self, conversation: Conversation) -> str:
        history = await self._repository.recent_for_phone(
            conversation.phone_number, limit=self._settings.conversation_history_limit
        )
        messages: list[ChatMessage] = []
        for past in history:
            messages.append(ChatMessage(role="user", content=past.incoming_message))
            if past.llm_response:
                messages.append(ChatMessage(role="assistant", content=past.llm_response))

        if not messages:  # pragma: no cover - the current turn is always stored first
            messages = [ChatMessage(role="user", content=conversation.incoming_message)]

        reply = await self._llm.generate(messages, system=self._settings.llm_system_prompt)
        return self._fit_sms(reply)

    async def _deliver(self, conversation: Conversation, reply: str) -> Conversation:
        try:
            result = await self._sms.send(conversation.phone_number, reply)
        except SmsProviderError as exc:
            logger.error(
                "reply delivery failed",
                extra={"conversationId": conversation.id, "error": str(exc)},
            )
            # The conversation stays in storage with a failed delivery, so it can be
            # retried or inspected by an admin instead of disappearing.
            return await self._repository.update(
                conversation.id,
                ConversationUpdate(
                    delivery_status=DeliveryStatus.FAILED, error_message=str(exc)[:500]
                ),
            )

        return await self._repository.update(
            conversation.id,
            ConversationUpdate(
                provider_response_id=result.message_id, delivery_status=result.status
            ),
        )

    def _fit_sms(self, text: str) -> str:
        """Keep a reply within a sane number of SMS segments."""
        limit = self._settings.max_reply_length
        text = text.strip()
        return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."
