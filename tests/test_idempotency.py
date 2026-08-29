"""Repeated webhook deliveries must not produce repeated work.

Carriers retry: Twilio re-posts a webhook when the first attempt is slow or returns an
error, and the same message id arrives twice.
"""

from __future__ import annotations

import asyncio

from app.domain.enums import ConversationStatus
from tests.conftest import PHONE

QUESTION = "How do I reset my password?"


def _payload(message_id: str = "SM_DUP", body: str = QUESTION) -> dict:
    return {"from": PHONE, "body": body, "messageId": message_id}


class TestDuplicateWebhooks:
    async def test_the_second_delivery_is_reported_as_a_duplicate(self, client):
        first = await client.post("/webhooks/sms", json=_payload())
        second = await client.post("/webhooks/sms", json=_payload())

        assert first.json()["status"] == "accepted"
        assert second.json()["status"] == "duplicate"
        assert second.json()["conversationId"] == first.json()["conversationId"]

    async def test_only_one_conversation_is_stored(self, client, repository):
        await client.post("/webhooks/sms", json=_payload())
        await client.post("/webhooks/sms", json=_payload())

        stored = await repository.list_by_phone(PHONE)
        assert len(stored) == 1
        assert stored[0].status is ConversationStatus.COMPLETED

    async def test_the_llm_is_called_once(self, client, llm):
        await client.post("/webhooks/sms", json=_payload())
        await client.post("/webhooks/sms", json=_payload())

        assert len(llm.calls) == 1

    async def test_the_reply_is_sent_once(self, client, sms):
        await client.post("/webhooks/sms", json=_payload())
        await client.post("/webhooks/sms", json=_payload())

        assert len(sms.outbox) == 1

    async def test_a_different_message_id_is_a_new_conversation(self, client, repository):
        await client.post("/webhooks/sms", json=_payload("SM1"))
        await client.post("/webhooks/sms", json=_payload("SM2", "What are your support hours?"))

        assert len(await repository.list_by_phone(PHONE)) == 2

    async def test_concurrent_deliveries_of_the_same_message(self, client, repository, sms):
        """The unique provider_message_id is what makes the race safe, not the lookup."""
        await asyncio.gather(
            *(client.post("/webhooks/sms", json=_payload("SM_RACE")) for _ in range(5))
        )

        assert len(await repository.list_by_phone(PHONE)) == 1
        assert len(sms.outbox) == 1

    async def test_duplicate_feedback_is_not_applied_twice(self, client, repository, sms):
        await client.post("/webhooks/sms", json=_payload("SM1"))
        first = await client.post("/webhooks/sms", json=_payload("SM_FB", "\U0001f44d"))
        second = await client.post("/webhooks/sms", json=_payload("SM_FB", "\U0001f44d"))

        assert (first.json()["status"], second.json()["status"]) == ("feedback", "duplicate")
        # One answer plus one acknowledgement -- the repeated rating is ignored.
        assert len(sms.outbox) == 2
        assert len(await repository.list_by_phone(PHONE)) == 1

    async def test_a_second_rating_from_a_new_message_still_applies(self, client, repository):
        """Deduplication is per inbound SMS, not a lock on the rating itself."""
        await client.post("/webhooks/sms", json=_payload("SM1"))
        await client.post("/webhooks/sms", json=_payload("SM_FB1", "\U0001f44d"))
        await client.post("/webhooks/sms", json=_payload("SM_FB2", "\U0001f44e"))

        latest = await repository.get_latest_for_phone(PHONE)
        assert latest.feedback.value == "negative"


class TestTwilioDuplicates:
    async def test_a_retried_twilio_webhook_is_a_duplicate(self, client, repository, sms):
        form = {"From": PHONE, "Body": QUESTION, "MessageSid": "SM_TWILIO_DUP"}

        first = await client.post("/webhooks/sms/twilio", data=form)
        second = await client.post("/webhooks/sms/twilio", data=form)

        assert (first.status_code, second.status_code) == (200, 200)
        assert len(await repository.list_by_phone(PHONE)) == 1
        assert len(sms.outbox) == 1
