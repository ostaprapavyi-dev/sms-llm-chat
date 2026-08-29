"""Deterministic LLM used by the test suite and by ``LLM_PROVIDER=mock`` locally.

Keyword rules keep the canned answers plausible for a support assistant while staying
fully predictable, so tests can assert on exact text without touching the network.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.providers.llm.base import ChatMessage

DEFAULT_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("password", "reset", "login", "sign in"),
        "You can reset your password by clicking 'Forgot password' on the login page.",
    ),
    (
        ("invoice", "billing", "payment", "refund"),
        "Invoices and payment details are available under Billing in your account settings.",
    ),
    (
        ("hours", "open", "support", "contact"),
        "Our support team is available Monday to Friday, 9:00-17:00 CET.",
    ),
    (
        ("hello", "hi", "hey"),
        "Hi! Ask me anything about your account and I will do my best to help.",
    ),
)

DEFAULT_REPLY = (
    "Thanks for your message! A support agent will follow up with more details shortly."
)


class MockLLMProvider:
    """Rule-based stand-in for a real model."""

    name = "mock"

    def __init__(
        self,
        rules: Iterable[tuple[tuple[str, ...], str]] = DEFAULT_RULES,
        default_reply: str = DEFAULT_REPLY,
    ) -> None:
        self._rules = tuple(rules)
        self._default_reply = default_reply
        # Recorded for assertions: what the service actually sent to the model.
        self.calls: list[tuple[list[ChatMessage], str | None]] = []

    async def generate(self, messages: list[ChatMessage], *, system: str | None = None) -> str:
        self.calls.append((list(messages), system))

        last_user_message = next(
            (message.content for message in reversed(messages) if message.role == "user"), ""
        )
        haystack = last_user_message.lower()
        for keywords, reply in self._rules:
            if any(keyword in haystack for keyword in keywords):
                return reply
        return self._default_reply
