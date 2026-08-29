"""Admin conversation log: authentication, authorization and filtering."""

from __future__ import annotations

import pytest

from app.domain.enums import ConversationStatus, Feedback
from app.domain.models import Conversation
from tests.conftest import ADMIN_AUTH, PHONE

OTHER_PHONE = "+40999888777"
ENDPOINT = "/admin/conversations"


@pytest.fixture
async def seeded(repository):
    """Two conversations for our phone number, one for somebody else."""
    for index, (phone, question, answer, feedback) in enumerate(
        [
            (PHONE, "How do I reset my password?", "Use 'Forgot password'.", Feedback.POSITIVE),
            (PHONE, "What are your support hours?", "Mon-Fri, 9-17 CET.", Feedback.NONE),
            (OTHER_PHONE, "Where is my invoice?", "Under Billing.", Feedback.NEGATIVE),
        ]
    ):
        await repository.create(
            Conversation(
                phone_number=phone,
                incoming_message=question,
                llm_response=answer,
                provider_message_id=f"SM{index}",
                provider_response_id=f"SM_OUT_{index}",
                status=ConversationStatus.COMPLETED,
                feedback=feedback,
            )
        )
    return repository


class TestAuthorization:
    async def test_anonymous_access_is_rejected(self, client, seeded):
        response = await client.get(ENDPOINT, params={"phoneNumber": PHONE})

        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Basic"

    @pytest.mark.parametrize(
        "credentials", [("admin", "wrong"), ("intruder", "password"), ("", "")]
    )
    async def test_wrong_credentials_are_rejected(self, client, seeded, credentials):
        response = await client.get(ENDPOINT, params={"phoneNumber": PHONE}, auth=credentials)

        assert response.status_code == 401

    async def test_valid_credentials_are_accepted(self, client, seeded):
        response = await client.get(ENDPOINT, params={"phoneNumber": PHONE}, auth=ADMIN_AUTH)

        assert response.status_code == 200

    async def test_credentials_are_required_even_for_an_unknown_number(self, client):
        response = await client.get(ENDPOINT, params={"phoneNumber": "+10000000000"})

        assert response.status_code == 401


class TestConversationLog:
    async def test_returns_only_the_requested_number(self, client, seeded):
        response = await client.get(ENDPOINT, params={"phoneNumber": PHONE}, auth=ADMIN_AUTH)
        body = response.json()

        assert body["phoneNumber"] == PHONE
        assert body["count"] == 2
        assert {item["phoneNumber"] for item in body["conversations"]} == {PHONE}

    async def test_exposes_messages_answers_timestamps_and_feedback(self, client, seeded):
        body = (
            await client.get(ENDPOINT, params={"phoneNumber": PHONE}, auth=ADMIN_AUTH)
        ).json()
        newest = body["conversations"][0]

        assert newest["incomingMessage"] == "What are your support hours?"
        assert newest["llmResponse"] == "Mon-Fri, 9-17 CET."
        assert newest["status"] == "completed"
        assert newest["feedback"] == "none"
        assert newest["providerMessageId"] == "SM1"
        assert newest["createdAt"] and newest["updatedAt"]

    async def test_feedback_is_visible(self, client, seeded):
        body = (
            await client.get(ENDPOINT, params={"phoneNumber": PHONE}, auth=ADMIN_AUTH)
        ).json()

        rated = [item for item in body["conversations"] if item["feedback"] != "none"]
        assert [item["incomingMessage"] for item in rated] == ["How do I reset my password?"]

    async def test_newest_first(self, client, seeded):
        body = (
            await client.get(ENDPOINT, params={"phoneNumber": PHONE}, auth=ADMIN_AUTH)
        ).json()

        assert [item["providerMessageId"] for item in body["conversations"]] == ["SM1", "SM0"]

    async def test_accepts_a_loosely_formatted_number(self, client, seeded):
        response = await client.get(
            ENDPOINT, params={"phoneNumber": "0036 123-456-789"}, auth=ADMIN_AUTH
        )

        assert response.json()["phoneNumber"] == PHONE
        assert response.json()["count"] == 2

    async def test_unknown_number_returns_an_empty_list(self, client, seeded):
        body = (
            await client.get(ENDPOINT, params={"phoneNumber": "+10000000000"}, auth=ADMIN_AUTH)
        ).json()

        assert (body["count"], body["conversations"]) == (0, [])

    async def test_invalid_number_is_rejected(self, client, seeded):
        response = await client.get(
            ENDPOINT, params={"phoneNumber": "not-a-number"}, auth=ADMIN_AUTH
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_phone_number"

    async def test_missing_query_parameter_is_rejected(self, client, seeded):
        response = await client.get(ENDPOINT, auth=ADMIN_AUTH)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    async def test_pagination(self, client, seeded):
        page = (
            await client.get(
                ENDPOINT,
                params={"phoneNumber": PHONE, "limit": 1, "offset": 1},
                auth=ADMIN_AUTH,
            )
        ).json()

        assert page["count"] == 1
        assert page["conversations"][0]["providerMessageId"] == "SM0"

    async def test_rejects_a_nonsense_limit(self, client, seeded):
        response = await client.get(
            ENDPOINT, params={"phoneNumber": PHONE, "limit": 0}, auth=ADMIN_AUTH
        )

        assert response.status_code == 422


class TestEndToEnd:
    async def test_a_webhook_conversation_shows_up_in_the_admin_log(self, client):
        await client.post(
            "/webhooks/sms",
            json={"from": PHONE, "body": "How do I reset my password?", "messageId": "SM_E2E"},
        )
        await client.post(
            "/webhooks/sms",
            json={"from": PHONE, "body": "\U0001f44d", "messageId": "SM_E2E_FB"},
        )

        body = (
            await client.get(ENDPOINT, params={"phoneNumber": PHONE}, auth=ADMIN_AUTH)
        ).json()

        assert body["count"] == 1
        record = body["conversations"][0]
        assert record["incomingMessage"] == "How do I reset my password?"
        assert "Forgot password" in record["llmResponse"]
        assert record["status"] == "completed"
        assert record["feedback"] == "positive"
