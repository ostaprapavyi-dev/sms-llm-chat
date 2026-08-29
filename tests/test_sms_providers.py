"""Unit tests for the SMS abstraction: payload parsing, signatures, sending, factory."""

from __future__ import annotations

import pytest
from twilio.base.exceptions import TwilioRestException
from twilio.request_validator import RequestValidator

from app.config import Settings
from app.core.errors import (
    ConfigurationError,
    InvalidPhoneNumberError,
    InvalidWebhookError,
    SignatureVerificationError,
    SmsProviderError,
)
from app.domain.enums import DeliveryStatus
from app.providers.sms.base import SmsProvider, parse_generic_payload
from app.providers.sms.factory import build_sms_provider
from app.providers.sms.mock import MockSmsProvider
from app.providers.sms.twilio import SIGNATURE_HEADER, TwilioProvider, build_twiml

WEBHOOK_URL = "https://example.test/webhooks/sms/twilio"
AUTH_TOKEN = "test-auth-token"

TWILIO_FORM = {
    "From": "+36123456789",
    "Body": "How do I reset my password?",
    "MessageSid": "SM123456789",
    "AccountSid": "AC123",
}


class _FakeMessage:
    def __init__(self, sid: str = "SM_OUT_1", status: str = "queued") -> None:
        self.sid = sid
        self.status = status


class _FakeMessages:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self._result = result or _FakeMessage()
        self._error = error
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return self._result


class _FakeTwilioClient:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


def _twilio(messages: _FakeMessages | None = None, **overrides) -> TwilioProvider:
    kwargs = {
        "account_sid": "AC123",
        "auth_token": AUTH_TOKEN,
        "from_number": "+15550001111",
        "client": _FakeTwilioClient(messages or _FakeMessages()),
    }
    kwargs.update(overrides)
    return TwilioProvider(**kwargs)


class TestGenericPayloadParsing:
    def test_parses_the_documented_shape(self):
        message = parse_generic_payload(
            {
                "from": "+36123456789",
                "body": "How do I reset my password?",
                "messageId": "SM123456789",
                "timestamp": "2026-07-27T12:00:00Z",
            }
        )
        assert message.phone_number == "+36123456789"
        assert message.provider_message_id == "SM123456789"
        assert message.timestamp is not None and message.timestamp.year == 2026

    def test_normalises_the_sender_number(self):
        message = parse_generic_payload(
            {"from": "0036 123-456-789", "body": "hi", "messageId": "SM1"}
        )
        assert message.phone_number == "+36123456789"

    def test_timestamp_is_optional(self):
        payload = {"from": "+36123456789", "body": "hi", "messageId": "SM1"}
        assert parse_generic_payload(payload).timestamp is None

    @pytest.mark.parametrize(
        "payload",
        [
            {"from": "+36123456789", "messageId": "SM1"},
            {"from": "+36123456789", "body": "hi"},
        ],
    )
    def test_missing_required_fields_are_rejected(self, payload):
        with pytest.raises(InvalidWebhookError):
            parse_generic_payload(payload)

    def test_invalid_sender_is_rejected(self):
        with pytest.raises(InvalidPhoneNumberError):
            parse_generic_payload({"from": "not-a-number", "body": "hi", "messageId": "SM1"})

    def test_invalid_timestamp_is_rejected(self):
        with pytest.raises(InvalidWebhookError):
            parse_generic_payload(
                {"from": "+36123456789", "body": "hi", "messageId": "SM1", "timestamp": "yesterday"}
            )


class TestMockProvider:
    async def test_send_records_to_the_outbox(self):
        provider = MockSmsProvider()
        result = await provider.send("+36123456789", "Your answer")

        assert result.status is DeliveryStatus.SENT
        assert [(item.to, item.body) for item in provider.outbox] == [
            ("+36123456789", "Your answer")
        ]
        assert provider.outbox[0].message_id == result.message_id

    async def test_message_ids_are_unique(self):
        provider = MockSmsProvider()
        first = await provider.send("+36123456789", "a")
        second = await provider.send("+36123456789", "b")
        assert first.message_id != second.message_id

    def test_signature_check_is_a_no_op(self):
        MockSmsProvider().verify_signature(url="http://x", headers={}, payload={})

    def test_satisfies_the_protocol(self):
        assert isinstance(MockSmsProvider(), SmsProvider)


class TestTwilioWebhookParsing:
    def test_parses_the_form_payload(self):
        message = _twilio().parse_webhook(TWILIO_FORM)
        assert message.phone_number == "+36123456789"
        assert message.body == "How do I reset my password?"
        assert message.provider_message_id == "SM123456789"
        assert message.provider == "twilio"

    def test_accepts_sms_message_sid_alias(self):
        payload = {"From": "+36123456789", "Body": "hi", "SmsMessageSid": "SM999"}
        assert _twilio().parse_webhook(payload).provider_message_id == "SM999"

    def test_missing_message_sid_is_rejected(self):
        with pytest.raises(InvalidWebhookError):
            _twilio().parse_webhook({"From": "+36123456789", "Body": "hi"})

    def test_missing_body_is_rejected(self):
        with pytest.raises(InvalidWebhookError):
            _twilio().parse_webhook({"From": "+36123456789", "MessageSid": "SM1"})


class TestTwilioSignature:
    def test_accepts_a_valid_signature(self):
        signature = RequestValidator(AUTH_TOKEN).compute_signature(WEBHOOK_URL, TWILIO_FORM)
        _twilio().verify_signature(
            url=WEBHOOK_URL, headers={SIGNATURE_HEADER: signature}, payload=TWILIO_FORM
        )

    def test_rejects_a_wrong_signature(self):
        with pytest.raises(SignatureVerificationError):
            _twilio().verify_signature(
                url=WEBHOOK_URL, headers={SIGNATURE_HEADER: "bogus"}, payload=TWILIO_FORM
            )

    def test_rejects_a_missing_signature_header(self):
        with pytest.raises(SignatureVerificationError):
            _twilio().verify_signature(url=WEBHOOK_URL, headers={}, payload=TWILIO_FORM)

    def test_validation_can_be_switched_off(self):
        _twilio(validate_signature=False).verify_signature(
            url=WEBHOOK_URL, headers={}, payload=TWILIO_FORM
        )

    def test_validation_is_skipped_without_an_auth_token(self):
        provider = _twilio(auth_token="")
        assert provider.validate_signature is False
        provider.verify_signature(url=WEBHOOK_URL, headers={}, payload=TWILIO_FORM)


class TestTwilioSending:
    async def test_send_maps_the_twilio_status(self):
        messages = _FakeMessages(_FakeMessage(sid="SM_OUT_9", status="queued"))
        result = await _twilio(messages).send("+36123456789", "Your answer")

        assert (result.message_id, result.status) == ("SM_OUT_9", DeliveryStatus.QUEUED)
        assert messages.last_kwargs == {
            "to": "+36123456789",
            "from_": "+15550001111",
            "body": "Your answer",
        }

    async def test_unknown_status_does_not_break_the_result(self):
        messages = _FakeMessages(_FakeMessage(status="something-new"))
        assert (await _twilio(messages).send("+36123456789", "x")).status is DeliveryStatus.UNKNOWN

    async def test_provider_errors_become_sms_provider_error(self):
        error = TwilioRestException(status=400, uri="/Messages", msg="unverified number")
        with pytest.raises(SmsProviderError):
            await _twilio(_FakeMessages(error=error)).send("+36123456789", "x")

    def test_satisfies_the_protocol(self):
        assert isinstance(_twilio(), SmsProvider)


class TestTwiml:
    def test_renders_a_messaging_response(self):
        assert build_twiml("Your generated answer goes here.") == (
            '<?xml version="1.0" encoding="UTF-8"?><Response>'
            "<Message>Your generated answer goes here.</Message></Response>"
        )

    def test_escapes_markup_in_the_answer(self):
        assert "<script>" not in build_twiml("<script>alert(1)</script>")


class TestFactory:
    def test_builds_the_mock_provider_by_default(self):
        assert isinstance(build_sms_provider(Settings(_env_file=None)), MockSmsProvider)

    def test_builds_twilio_when_configured(self):
        settings = Settings(
            _env_file=None,
            sms_provider="twilio",
            twilio_account_sid="AC1",
            twilio_auth_token="token",
            twilio_phone_number="+15550001111",
        )
        provider = build_sms_provider(settings)
        assert isinstance(provider, TwilioProvider)
        assert provider.from_number == "+15550001111"

    def test_missing_twilio_credentials_are_a_configuration_error(self):
        settings = Settings(_env_file=None, sms_provider="twilio", twilio_account_sid="AC1")
        with pytest.raises(ConfigurationError) as exc:
            build_sms_provider(settings)
        assert "TWILIO_AUTH_TOKEN" in str(exc.value)
