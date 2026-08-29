"""The contract every LLM provider implements.

Deliberately tiny: one method that turns a dialogue into a reply. Anything an
individual vendor needs (base url, model name, retries) stays inside its own class,
so swapping providers never reaches the service layer.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

Role = Literal["user", "assistant"]


class ChatMessage(BaseModel):
    """One turn of the conversation handed to the model."""

    role: Role
    content: str


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def generate(self, messages: list[ChatMessage], *, system: str | None = None) -> str:
        """Return the assistant reply for ``messages``.

        Implementations must raise :class:`~app.core.errors.LLMError` for any failure,
        so callers never have to know vendor-specific exception types.
        """
        ...
