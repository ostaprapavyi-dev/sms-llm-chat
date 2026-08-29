"""How the service behaves when a provider or the database fails.

The rule everywhere: a failure must not lose the conversation and must not leave the
user without an answer.
"""

from __future__ import annotations

import asyncio

import pytest

from app.api.deps import get_llm_provider, get_repository, get_sms_provider
from app.core.errors import LLMError, SmsProviderError, StorageError
from app.db.repository import InMemoryConversationRepository
from app.domain.enums import ConversationStatus, DeliveryStatus
from app.providers.sms.base import SendResult
from app.providers.sms.twilio import SIGNATURE_HEADER, TwilioProvider
from tests.conftest import PHONE

FALLBACK = "Sorry, we could not generate an answer right now. Please try again in a few minutes."


class BrokenLLM:
    name = "broken"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or LLMError("the provider is down")
        self.calls = 0

    async def generate(self, messages, *, system=None):
        self.calls += 1
        raise self.error


class HangingLLM:
    name = "hanging"

    async def generate(self, messages, *, system=None):
        await asyncio.sleep(5)
        return "too late"


class BrokenSms:
    name = "broken"

    def __init__(self) -> None:
        self.attempts = 0

    async def send(self, to: str, body: str) -> SendResult:
        self.attempts += 1
        raise SmsProviderError("the carrier rejected the message")

    def parse_webhook(self, payload):  # pragma: no cover - not used by these tests
        raise NotImplementedError

    def verify_signature(self, *, url, headers, payload) -> None:
        return None


class BrokenRepository(InMemoryConversationRepository):
    async def create(self, conversation):
        raise StorageError("the database is unavailable")


async def _send_message(client, body: str = "How do I reset my password?", message_id="SM_ERR"):
    return await client.post(
        "/webhooks/sms", json={"from": PHONE, "body": body, "messageId": message_id}
    )


class TestLlmFailures:
    @pytest.fixture
    def broken_llm(self, app):
        provider = BrokenLLM()
        app.dependency_overrides[get_llm_provider] = lambda: provider
        yield provider
        app.dependency_overrides.clear()

    async def test_the_user_still_gets_an_answer(self, client, broken_llm, sms):
        await _send_message(client)

        assert [message.body for message in sms.outbox] == [FALLBACK]

    async def test_the_conversation_is_stored_as_failed(self, client, broken_llm, repository):
        response = await _send_message(client)
        conversation_id = response.json()["conversationId"]

        stored = await repository.get(conversation_id)
        assert stored.status is ConversationStatus.FAILED
        assert stored.llm_response is None
        assert "the provider is down" in stored.error_message

    async def test_the_webhook_itself_still_succeeds(self, client, broken_llm):
        """The carrier must not be told to retry -- we accepted the message."""
        assert (await _send_message(client)).status_code == 202

    async def test_an_unexpected_provider_exception_is_contained(self, client, app, sms):
        app.dependency_overrides[get_llm_provider] = lambda: BrokenLLM(RuntimeError("boom"))

        await _send_message(client)

        assert [message.body for message in sms.outbox] == [FALLBACK]
        app.dependency_overrides.clear()

    async def test_a_hanging_provider_is_cut_off_by_the_timeout(self, client, app, sms, repository):
        app.state.settings.llm_timeout_seconds = 0.05
        app.dependency_overrides[get_llm_provider] = lambda: HangingLLM()

        response = await _send_message(client)

        stored = await repository.get(response.json()["conversationId"])
        assert stored.status is ConversationStatus.FAILED
        assert "did not answer within" in stored.error_message
        assert [message.body for message in sms.outbox] == [FALLBACK]
        app.dependency_overrides.clear()


class TestSmsFailures:
    @pytest.fixture
    def broken_sms(self, app):
        provider = BrokenSms()
        app.dependency_overrides[get_sms_provider] = lambda: provider
        yield provider
        app.dependency_overrides.clear()

    async def test_the_answer_is_kept_even_though_delivery_failed(
        self, client, broken_sms, repository
    ):
        response = await _send_message(client)

        stored = await repository.get(response.json()["conversationId"])
        assert stored.status is ConversationStatus.COMPLETED
        assert "Forgot password" in stored.llm_response
        assert stored.delivery_status is DeliveryStatus.FAILED
        assert "carrier rejected" in stored.error_message

    async def test_delivery_is_attempted_exactly_once(self, client, broken_sms):
        await _send_message(client)

        assert broken_sms.attempts == 1

    async def test_a_failed_feedback_ack_does_not_break_the_webhook(self, client, broken_sms):
        await _send_message(client, "How do I reset my password?", "SM1")

        response = await _send_message(client, "\U0001f44d", "SM2")

        assert response.status_code == 202
        assert response.json()["status"] == "feedback"


class TestStorageFailures:
    async def test_a_storage_failure_is_reported_as_a_server_error(self, client, app):
        app.dependency_overrides[get_repository] = lambda: BrokenRepository()

        response = await _send_message(client)

        assert response.status_code == 500
        error = response.json()["error"]
        assert error["code"] == "storage_error"
        assert error["requestId"]
        app.dependency_overrides.clear()

    async def test_the_admin_endpoint_reports_storage_failures_too(self, client, app):
        class UnreadableRepository(InMemoryConversationRepository):
            async def list_by_phone(self, phone_number, *, limit=50, offset=0):
                raise StorageError("the database is unavailable")

        app.dependency_overrides[get_repository] = lambda: UnreadableRepository()

        response = await client.get(
            "/admin/conversations", params={"phoneNumber": PHONE}, auth=("admin", "password")
        )

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "storage_error"
        app.dependency_overrides.clear()


class TestWebhookValidation:
    @pytest.mark.parametrize(
        ("payload", "code"),
        [
            ({"from": PHONE, "messageId": "SM1"}, "invalid_webhook"),
            ({"from": PHONE, "body": "hi"}, "invalid_webhook"),
            ({"from": "not-a-number", "body": "hi", "messageId": "SM1"}, "invalid_phone_number"),
            (
                {"from": PHONE, "body": "hi", "messageId": "SM1", "timestamp": "yesterday"},
                "invalid_webhook",
            ),
        ],
    )
    async def test_bad_payloads_are_rejected_with_a_readable_error(self, client, payload, code):
        response = await client.post("/webhooks/sms", json=payload)

        assert response.status_code == 400
        assert response.json()["error"]["code"] == code

    async def test_a_non_object_body_is_a_validation_error(self, client):
        response = await client.post("/webhooks/sms", json=["not", "an", "object"])

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    async def test_nothing_is_stored_for_a_rejected_payload(self, client, repository):
        await client.post("/webhooks/sms", json={"from": PHONE, "messageId": "SM1"})

        assert await repository.list_by_phone(PHONE) == []


class TestTwilioSignatureFailures:
    async def test_a_forged_request_is_rejected(self, client, app, repository):
        app.state.twilio_parser = TwilioProvider(
            account_sid="AC1", auth_token="secret", from_number="+15550001111"
        )

        response = await client.post(
            "/webhooks/sms/twilio",
            data={"From": PHONE, "Body": "hi", "MessageSid": "SM1"},
            headers={SIGNATURE_HEADER: "forged"},
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "invalid_signature"
        assert await repository.list_by_phone(PHONE) == []


class TestRequestCorrelation:
    async def test_the_request_id_is_echoed_and_reused(self, client):
        response = await client.post(
            "/webhooks/sms",
            json={"from": PHONE, "messageId": "SM1"},
            headers={"X-Request-ID": "trace-me"},
        )

        assert response.headers["X-Request-ID"] == "trace-me"
        assert response.json()["error"]["requestId"] == "trace-me"
