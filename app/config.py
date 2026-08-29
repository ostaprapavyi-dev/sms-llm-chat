"""Application settings.

All configuration comes from environment variables (or a local ``.env`` file), so the
same image can be pointed at a mock provider in tests and a real one in production
without touching code.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

SmsProviderName = Literal["mock", "twilio"]
SmsReplyMode = Literal["api", "twiml"]
LlmProviderName = Literal["mock", "groq", "openai"]

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful customer support assistant replying over SMS. "
    "Answer in plain text, no markdown, and keep the reply under 300 characters."
)


class Settings(BaseSettings):
    """Typed view over the environment. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- server ---------------------------------------------------------
    port: int = 3000
    log_level: str = "INFO"
    debug_endpoints_enabled: bool = True

    # --- sms ------------------------------------------------------------
    sms_provider: SmsProviderName = "mock"
    sms_reply_mode: SmsReplyMode = "api"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    twilio_validate_signature: bool = True

    # --- llm ------------------------------------------------------------
    llm_provider: LlmProviderName = "mock"
    llm_model: str = ""
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2
    llm_system_prompt: str = DEFAULT_SYSTEM_PROMPT
    groq_api_key: str = ""
    openai_api_key: str = ""

    # --- storage --------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    conversation_history_limit: int = 10

    # --- admin ----------------------------------------------------------
    admin_username: str = "admin"
    admin_password: str = "password"

    # --- messages sent back to the user ---------------------------------
    fallback_reply: str = (
        "Sorry, we could not generate an answer right now. Please try again in a few minutes."
    )
    feedback_ack_reply: str = "Thanks for the feedback!"
    feedback_no_conversation_reply: str = (
        "Thanks! We could not find a recent answer to attach your feedback to."
    )


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance (cleared in tests via ``get_settings.cache_clear()``)."""
    return Settings()
