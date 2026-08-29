"""The Twilio route end to end: form payload, signature, both reply modes."""

from __future__ import annotations

import pytest
from twilio.request_validator import RequestValidator

from app.domain.enums import ConversationStatus, Feedback
from app.providers.sms.twilio import SIGNATURE_HEADER, TwilioProvider
from tests.conftest import PHONE

ENDPOINT = "/webhooks/sms/twilio"
QUESTION = "How do I reset my password?"
ANSWER = "You can reset your password by clicking 'Forgot password' on the login page."
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
AUTH_TOKEN = "test-auth-token"


def _form(body: str = QUESTION, message_sid: str = "SM_TW1", sender: str = PHONE) -> dict:
    return {"From": sender, "Body": body, "MessageSid": message_sid, "AccountSid": "AC1"}


class TestAsynchronousMode:
    async def test_twilio_gets_an_empty_twiml_immediately(self, client):
        response = await client.post(ENDPOINT, data=_form())

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/xml")
        assert response.text == EMPTY_TWIML

    async def test_the_conversation_is_stored_and_answered(self, client, repository, sms):
        await client.post(ENDPOINT, data=_form())

        stored = (await repository.list_by_phone(PHONE))[0]
        assert stored.incoming_message == QUESTION
        assert stored.llm_response == ANSWER
        assert stored.status is ConversationStatus.COMPLETED
        assert stored.provider_message_id == "SM_TW1"
        assert [message.body for message in sms.outbox] == [ANSWER]

    async def test_the_channel_prefix_is_stripped_from_the_sender(self, client, repository):
        await client.post(ENDPOINT, data=_form(sender=f"whatsapp:{PHONE}"))

        assert (await repository.list_by_phone(PHONE))[0].phone_number == PHONE

    async def test_feedback_over_the_twilio_route(self, client, repository, sms):
        await client.post(ENDPOINT, data=_form(QUESTION, "SM_TW1"))

        await client.post(ENDPOINT, data=_form("\U0001f44d", "SM_TW2"))

        assert (await repository.get_latest_for_phone(PHONE)).feedback is Feedback.POSITIVE
        assert sms.outbox[-1].body == "Thanks for the feedback!"

    @pytest.mark.parametrize(
        "form",
        [
            {"From": PHONE, "MessageSid": "SM1"},
            {"From": PHONE, "Body": "hi"},
            {"Body": "hi", "MessageSid": "SM1"},
        ],
    )
    async def test_incomplete_payloads_are_rejected(self, client, form):
        response = await client.post(ENDPOINT, data=form)

        assert response.status_code == 400
        assert response.json()["error"]["code"].startswith("invalid_")


class TestSynchronousTwimlMode:
    @pytest.fixture
    def twiml_mode(self, app):
        app.state.settings.sms_reply_mode = "twiml"
        yield app
        app.state.settings.sms_reply_mode = "api"

    async def test_the_answer_is_returned_as_twiml(self, client, twiml_mode):
        response = await client.post(ENDPOINT, data=_form())

        assert response.status_code == 200
        assert response.text == (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Message>{ANSWER}</Message></Response>"
        )

    async def test_no_message_is_sent_over_the_api(self, client, twiml_mode, sms):
        await client.post(ENDPOINT, data=_form())

        assert sms.outbox == []

    async def test_the_conversation_is_still_stored(self, client, twiml_mode, repository):
        await client.post(ENDPOINT, data=_form())

        assert (await repository.list_by_phone(PHONE))[0].llm_response == ANSWER

    async def test_the_answer_is_xml_escaped(self, client, twiml_mode, app):
        class MarkupLLM:
            name = "markup"

            async def generate(self, messages, *, system=None):
                return "Use <b>Forgot password</b> & try again"

        from app.api.deps import get_llm_provider

        app.dependency_overrides[get_llm_provider] = lambda: MarkupLLM()

        response = await client.post(ENDPOINT, data=_form())

        assert "<b>" not in response.text
        assert "&amp;" in response.text
        app.dependency_overrides.clear()


class TestSignatureVerification:
    @pytest.fixture
    def signed(self, app):
        """Enable real signature checking, as it would be in production."""
        app.state.twilio_parser = TwilioProvider(
            account_sid="AC1", auth_token=AUTH_TOKEN, from_number="+15550001111"
        )
        return app

    async def test_a_correctly_signed_request_is_accepted(self, client, signed, repository):
        form = _form()
        url = f"http://testserver{ENDPOINT}"
        signature = RequestValidator(AUTH_TOKEN).compute_signature(url, form)

        response = await client.post(ENDPOINT, data=form, headers={SIGNATURE_HEADER: signature})

        assert response.status_code == 200
        assert len(await repository.list_by_phone(PHONE)) == 1

    async def test_an_unsigned_request_is_rejected(self, client, signed):
        response = await client.post(ENDPOINT, data=_form())

        assert response.status_code == 403

    async def test_a_signature_for_another_payload_is_rejected(self, client, signed):
        url = f"http://testserver{ENDPOINT}"
        other = RequestValidator(AUTH_TOKEN).compute_signature(url, _form("different body"))

        response = await client.post(ENDPOINT, data=_form(), headers={SIGNATURE_HEADER: other})

        assert response.status_code == 403
