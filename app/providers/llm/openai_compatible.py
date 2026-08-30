"""Provider for any OpenAI-compatible chat completions API.

Groq and OpenAI speak the same wire protocol, so both are served by this one class with
a different base url and default model. Adding another OpenAI-compatible vendor (Together,
Fireworks, a local vLLM) is a new entry in :data:`PRESETS`, not new code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from openai import AsyncOpenAI, OpenAIError

from app.core.errors import LLMError
from app.providers.llm.base import ChatMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderPreset:
    """Vendor-specific defaults for the shared client."""

    base_url: str | None
    default_model: str
    api_key_field: str


PRESETS: dict[str, ProviderPreset] = {
    "groq": ProviderPreset(
        base_url="https://api.groq.com/openai/v1",
        default_model="openai/gpt-oss-20b",
        api_key_field="groq_api_key",
    ),
    "openai": ProviderPreset(
        base_url=None,  # the SDK default
        default_model="gpt-4o-mini",
        api_key_field="openai_api_key",
    ),
}


class OpenAICompatibleProvider:
    """Chat completions client wrapped in the project's error contract.

    Timeouts and exponential-backoff retries are delegated to the OpenAI SDK; whatever
    it still fails on is re-raised as :class:`LLMError` so the service layer can degrade
    gracefully without importing vendor exceptions.
    """

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 20.0,
        max_retries: int = 2,
        max_output_tokens: int = 300,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self._max_output_tokens = max_output_tokens
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    async def generate(self, messages: list[ChatMessage], *, system: str | None = None) -> str:
        payload: list[dict[str, str]] = []
        if system:
            payload.append({"role": "system", "content": system})
        payload.extend({"role": message.role, "content": message.content} for message in messages)

        try:
            completion = await self._client.chat.completions.create(
                model=self.model,
                messages=payload,
                max_tokens=self._max_output_tokens,
            )
        except OpenAIError as exc:
            logger.warning(
                "llm request failed", extra={"provider": self.name, "model": self.model}
            )
            raise LLMError(f"{self.name} request failed: {exc}") from exc

        choices = getattr(completion, "choices", None)
        content = choices[0].message.content if choices else None
        if not content or not content.strip():
            raise LLMError(f"{self.name} returned an empty response")
        return content.strip()
