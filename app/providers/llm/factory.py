"""Chooses the LLM provider from configuration.

The single place in the codebase that knows which vendors exist.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.core.errors import ConfigurationError
from app.providers.llm.base import LLMProvider
from app.providers.llm.mock import MockLLMProvider
from app.providers.llm.openai_compatible import PRESETS, OpenAICompatibleProvider

logger = logging.getLogger(__name__)


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Instantiate the provider named by ``LLM_PROVIDER``."""
    name = settings.llm_provider

    if name == "mock":
        logger.info("using mock llm provider")
        return MockLLMProvider()

    preset = PRESETS.get(name)
    if preset is None:  # pragma: no cover - Settings already restricts the value
        raise ConfigurationError(f"Unknown LLM provider '{name}'")

    api_key = getattr(settings, preset.api_key_field, "")
    if not api_key:
        raise ConfigurationError(
            f"LLM_PROVIDER={name} requires {preset.api_key_field.upper()} to be set"
        )

    model = settings.llm_model or preset.default_model
    logger.info("using llm provider", extra={"provider": name, "model": model})
    return OpenAICompatibleProvider(
        name=name,
        api_key=api_key,
        model=model,
        base_url=preset.base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_output_tokens=settings.llm_max_output_tokens,
    )
