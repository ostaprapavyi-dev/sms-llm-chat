"""Chooses the SMS provider from configuration."""

from __future__ import annotations

import logging

from app.config import Settings
from app.core.errors import ConfigurationError
from app.providers.sms.base import SmsProvider
from app.providers.sms.mock import MockSmsProvider
from app.providers.sms.twilio import TwilioProvider

logger = logging.getLogger(__name__)

_REQUIRED_TWILIO_FIELDS = ("twilio_account_sid", "twilio_auth_token", "twilio_phone_number")


def build_sms_provider(settings: Settings) -> SmsProvider:
    """Instantiate the provider named by ``SMS_PROVIDER``."""
    name = settings.sms_provider

    if name == "mock":
        logger.info("using mock sms provider")
        return MockSmsProvider()

    if name == "twilio":
        missing = [
            field.upper() for field in _REQUIRED_TWILIO_FIELDS if not getattr(settings, field)
        ]
        if missing:
            raise ConfigurationError(f"SMS_PROVIDER=twilio requires {', '.join(missing)}")
        logger.info("using twilio sms provider", extra={"from": settings.twilio_phone_number})
        return TwilioProvider(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            from_number=settings.twilio_phone_number,
            validate_signature=settings.twilio_validate_signature,
        )

    raise ConfigurationError(f"Unknown SMS provider '{name}'")  # pragma: no cover
