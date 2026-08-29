"""The core flow: an SMS arrives, an answer is generated, stored and sent back."""

from __future__ import annotations

import pytest

from app.api.deps import get_llm_provider
from app.domain.enums import ConversationStatus, DeliveryStatus, Feedback
from app.providers.llm.mock import DEFAULT_REPLY
from tests.conftest import PHONE

QUESTION = "How do I reset my password?"
ANSWER = "You can reset your password by clicking 'Forgot password' on the login page."


class VerboseLLM:
    """Answers with more text than fits in a few SMS segments."""

    name = "verbose"

    def __init__(self, length: int = 1000) -> None:
        self.length = length

    async def generate(self, messages, *, system=None):
        return "x" * self.length


async def _ask(client, body: str = QUESTION, message_id: str = "SM1", phone: str = PHONE):
    return await client.post(
        "/webhooks/sms",
        json={"from": phone, "body": body, "messageId": message_id},
    )


class TestHappyPath:
    async def test_the_webhook_is_accepted(self, client):
        response = await _ask(client)

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "accepted"
        assert body["conversationId"].startswith("conv_")

    async def test_the_conversation_is_stored_completely(self, client, repository):
        response = await _ask(client)

        stored = await repository.get(response.json()["conversationId"])
        assert stored.phone_number == PHONE
        assert stored.incoming_message == QUESTION
        assert stored.llm_response == ANSWER
        assert stored.provider_message_id == "SM1"
        assert stored.status is ConversationStatus.COMPLETED
        assert stored.feedback is Feedback.NONE
        assert stored.created_at is not None

    async def test_the_answer_is_sent_back_to_the_sender(self, client, sms):
        await _ask(client)

        assert [(message.to, message.body) for message in sms.outbox] == [(PHONE, ANSWER)]

    async def test_the_delivery_result_is_recorded(self, client, repository, sms):
        response = await _ask(client)

        stored = await repository.get(response.json()["conversationId"])
        assert stored.delivery_status is DeliveryStatus.SENT
        assert stored.provider_response_id == sms.outbox[0].message_id

    async def test_the_documented_payload_shape_works_verbatim(self, client, repository):
        response = await client.post(
            "/webhooks/sms",
            json={
                "from": "+36123456789",
                "body": "How do I reset my password?",
                "messageId": "SM123456789",
                "timestamp": "2026-07-27T12:00:00Z",
            },
        )

        assert response.status_code == 202
        stored = await repository.get(response.json()["conversationId"])
        assert stored.provider_message_id == "SM123456789"

    async def test_a_loosely_formatted_sender_is_normalised(self, client, repository):
        response = await _ask(client, phone="0036 123-456-789")

        stored = await repository.get(response.json()["conversationId"])
        assert stored.phone_number == PHONE

    async def test_the_timestamp_is_optional(self, client):
        assert (await _ask(client)).status_code == 202


class TestDialogueContext:
    async def test_previous_turns_are_sent_to_the_model(self, client, llm):
        await _ask(client, QUESTION, "SM1")
        await _ask(client, "What are your support hours?", "SM2")

        messages, _ = llm.calls[-1]
        assert [(m.role, m.content) for m in messages] == [
            ("user", QUESTION),
            ("assistant", ANSWER),
            ("user", "What are your support hours?"),
        ]

    async def test_the_system_prompt_is_passed(self, client, llm):
        await _ask(client)

        _, system = llm.calls[-1]
        assert "SMS" in system

    async def test_history_is_scoped_to_the_sender(self, client, llm):
        await _ask(client, QUESTION, "SM1", phone="+40999888777")
        await _ask(client, "What are your support hours?", "SM2", phone=PHONE)

        messages, _ = llm.calls[-1]
        assert [m.content for m in messages] == ["What are your support hours?"]

    async def test_history_is_capped_by_configuration(self, client, llm, app):
        app.state.settings.conversation_history_limit = 2

        for index in range(4):
            await _ask(client, f"question {index}", f"SM{index}")

        messages, _ = llm.calls[-1]
        # Two conversations: one answered pair plus the turn being answered now.
        assert [m.content for m in messages] == ["question 2", DEFAULT_REPLY, "question 3"]


class TestReplyShape:
    async def test_a_long_answer_is_truncated_to_fit_sms(self, client, app, sms, repository):
        app.dependency_overrides[get_llm_provider] = lambda: VerboseLLM()

        response = await _ask(client)

        limit = app.state.settings.max_reply_length
        stored = await repository.get(response.json()["conversationId"])
        assert len(stored.llm_response) == limit
        assert stored.llm_response.endswith("...")
        assert sms.outbox[0].body == stored.llm_response
        app.dependency_overrides.clear()

    async def test_a_short_answer_is_left_alone(self, client, sms):
        await _ask(client)

        assert sms.outbox[0].body == ANSWER


class TestSynchronousReplyMode:
    @pytest.fixture
    def twiml_mode(self, app):
        app.state.settings.sms_reply_mode = "twiml"
        yield app
        app.state.settings.sms_reply_mode = "api"

    async def test_the_answer_comes_back_in_the_response(self, client, twiml_mode):
        response = await _ask(client)

        body = response.json()
        assert body["status"] == "completed"
        assert body["reply"] == ANSWER

    async def test_nothing_is_sent_over_the_api(self, client, twiml_mode, sms):
        await _ask(client)

        assert sms.outbox == []

    async def test_the_conversation_is_still_stored(self, client, twiml_mode, repository):
        response = await _ask(client)

        stored = await repository.get(response.json()["conversationId"])
        assert stored.status is ConversationStatus.COMPLETED
        assert stored.delivery_status is DeliveryStatus.QUEUED
