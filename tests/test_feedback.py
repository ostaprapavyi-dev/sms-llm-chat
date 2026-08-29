"""Feedback classification and how the service records it."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.db.repository import InMemoryConversationRepository
from app.domain.enums import ConversationStatus, Feedback
from app.domain.models import Conversation, InboundMessage
from app.providers.llm.mock import MockLLMProvider
from app.providers.sms.mock import MockSmsProvider
from app.services.conversation_service import ConversationService, MessageKind
from app.services.feedback import classify_feedback


@pytest.fixture
def service():
    repository = InMemoryConversationRepository()
    llm = MockLLMProvider()
    sms = MockSmsProvider()
    service = ConversationService(
        repository=repository, llm=llm, sms=sms, settings=Settings(_env_file=None)
    )
    # Exposed for assertions; the service itself only knows the protocols.
    service.repository = repository
    service.llm = llm
    service.sms = sms
    return service


def _inbound(body: str, message_id: str = "SM_FB", phone: str = "+36123456789") -> InboundMessage:
    return InboundMessage(phone_number=phone, body=body, provider_message_id=message_id)


class TestClassification:
    @pytest.mark.parametrize("body", ["\U0001f44d", "1", "yes", "Y", " ok ", "GOOD", "+"])
    def test_positive_tokens(self, body):
        assert classify_feedback(body) is Feedback.POSITIVE

    @pytest.mark.parametrize("body", ["\U0001f44e", "0", "no", "N", "bad", "-", "wrong"])
    def test_negative_tokens(self, body):
        assert classify_feedback(body) is Feedback.NEGATIVE

    def test_skin_tone_modifier_is_ignored(self):
        assert classify_feedback("\U0001f44d\U0001f3fd") is Feedback.POSITIVE

    @pytest.mark.parametrize(
        "body",
        [
            "1 more question please",
            "no idea how to reset my password",
            "How do I reset my password?",
            "",
            "   ",
        ],
    )
    def test_questions_are_not_feedback(self, body):
        assert classify_feedback(body) is None


class TestRecording:
    async def test_attaches_to_the_most_recent_conversation(self, service):
        await service.handle_inbound(_inbound("How do I reset my password?", "SM1"))
        await service.handle_inbound(_inbound("What are your support hours?", "SM2"))

        result = await service.handle_inbound(_inbound("\U0001f44d", "SM3"))

        assert result.kind is MessageKind.FEEDBACK
        stored = await service.repository.list_by_phone("+36123456789")
        assert [(c.incoming_message, c.feedback) for c in stored] == [
            ("What are your support hours?", Feedback.POSITIVE),
            ("How do I reset my password?", Feedback.NONE),
        ]

    async def test_negative_feedback_is_recorded(self, service):
        await service.handle_inbound(_inbound("How do I reset my password?", "SM1"))
        await service.handle_inbound(_inbound("0", "SM2"))

        latest = await service.repository.get_latest_for_phone("+36123456789")
        assert latest.feedback is Feedback.NEGATIVE

    async def test_feedback_is_not_stored_as_a_conversation(self, service):
        await service.handle_inbound(_inbound("How do I reset my password?", "SM1"))
        await service.handle_inbound(_inbound("\U0001f44d", "SM2"))

        assert len(await service.repository.list_by_phone("+36123456789")) == 1

    async def test_feedback_does_not_call_the_llm(self, service):
        await service.handle_inbound(_inbound("How do I reset my password?", "SM1"))
        calls_before = len(service.llm.calls)

        await service.handle_inbound(_inbound("\U0001f44d", "SM2"))

        assert len(service.llm.calls) == calls_before

    async def test_feedback_is_acknowledged_over_sms(self, service):
        await service.handle_inbound(_inbound("How do I reset my password?", "SM1"))
        await service.handle_inbound(_inbound("\U0001f44d", "SM2"))

        assert service.sms.outbox[-1].body == "Thanks for the feedback!"

    async def test_feedback_without_a_previous_conversation(self, service):
        result = await service.handle_inbound(_inbound("\U0001f44d", "SM1"))

        assert result.kind is MessageKind.FEEDBACK
        assert result.conversation is None
        assert "could not find" in service.sms.outbox[-1].body

    async def test_feedback_only_touches_the_senders_own_conversation(self, service):
        await service.handle_inbound(_inbound("question from A", "SM1", "+36123456789"))
        await service.handle_inbound(_inbound("question from B", "SM2", "+40999888777"))

        await service.handle_inbound(_inbound("\U0001f44e", "SM3", "+40999888777"))

        a = await service.repository.get_latest_for_phone("+36123456789")
        b = await service.repository.get_latest_for_phone("+40999888777")
        assert (a.feedback, b.feedback) == (Feedback.NONE, Feedback.NEGATIVE)

    async def test_a_question_that_starts_with_a_digit_still_reaches_the_llm(self, service):
        result = await service.handle_inbound(_inbound("1 more thing: my invoice?", "SM1"))

        assert result.kind is MessageKind.QUESTION
        assert result.conversation.status is ConversationStatus.COMPLETED
        assert "Billing" in result.reply

    async def test_feedback_can_be_updated_by_a_later_rating(self, service):
        await service.handle_inbound(_inbound("How do I reset my password?", "SM1"))
        await service.handle_inbound(_inbound("\U0001f44d", "SM2"))
        await service.handle_inbound(_inbound("\U0001f44e", "SM3"))

        latest = await service.repository.get_latest_for_phone("+36123456789")
        assert latest.feedback is Feedback.NEGATIVE

    async def test_rating_lands_on_the_conversation_the_user_saw(self, service):
        """The rated conversation is the latest one, not a half-finished newer record."""
        await service.handle_inbound(_inbound("How do I reset my password?", "SM1"))
        stored = await service.repository.get_latest_for_phone("+36123456789")

        await service.handle_inbound(_inbound("1", "SM2"))

        rated = await service.repository.get(stored.id)
        assert rated.feedback is Feedback.POSITIVE
        assert rated.llm_response is not None


class TestConversationRecord:
    async def test_completed_conversation_carries_no_feedback_by_default(self, service):
        result = await service.handle_inbound(_inbound("How do I reset my password?", "SM1"))

        stored = await service.repository.get(result.conversation.id)
        assert stored.feedback is Feedback.NONE
        assert stored.status is ConversationStatus.COMPLETED

    async def test_seeded_conversation_can_be_rated(self, service):
        seeded = await service.repository.create(
            Conversation(
                phone_number="+36123456789",
                incoming_message="older question",
                llm_response="older answer",
                provider_message_id="SEED",
                status=ConversationStatus.COMPLETED,
            )
        )

        await service.handle_inbound(_inbound("yes", "SM1"))

        assert (await service.repository.get(seeded.id)).feedback is Feedback.POSITIVE
