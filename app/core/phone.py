"""Phone number normalisation.

The phone number is the conversation key, so "+36 123 456 789", "0036123456789" and
"+36123456789" must all resolve to the same record.
"""

from __future__ import annotations

import re

from app.core.errors import InvalidPhoneNumberError

_NON_DIGITS = re.compile(r"[^\d+]")
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_phone(raw: str | None) -> str:
    """Return ``raw`` as an E.164 number, or raise :class:`InvalidPhoneNumberError`.

    Accepts common separators, a leading ``00`` international prefix, and the
    ``whatsapp:``/``sms:`` channel prefixes Twilio puts in front of the number.
    """
    if not raw or not raw.strip():
        raise InvalidPhoneNumberError("Phone number is missing")

    value = raw.strip()
    if ":" in value:  # e.g. "whatsapp:+36123456789"
        value = value.split(":", 1)[1]

    value = _NON_DIGITS.sub("", value)
    if value.startswith("00"):
        value = "+" + value[2:]
    elif not value.startswith("+"):
        value = "+" + value

    # A "+" may only lead; strip any that survived from odd input like "+36+123".
    value = "+" + value[1:].replace("+", "")

    if not _E164.match(value):
        raise InvalidPhoneNumberError(f"'{raw}' is not a valid phone number")
    return value
